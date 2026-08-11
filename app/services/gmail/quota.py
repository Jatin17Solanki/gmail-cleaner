"""
Gmail API Quota Awareness
--------------------------
PRD Section 7: a rolling 60-second usage counter against Gmail's
6,000-units/minute/user cap, proactive blocking (with a live wait-time
message) before a call would exceed it, and reactive 429/403 backoff as a
safety net. Also the fix for scans silently under-counting senders when a
batched metadata fetch gets rate-limited mid-scan (see delete.py/archive.py/
mark_read.py's scan functions) — a sub-request that fails with a
quota-shaped error now gets one retry pass instead of being dropped.

Cost figures are PRD Section 7's own table where given (messages.list=5,
messages.get=20, messages.batchModify=50, flat regardless of batch size).
The PRD doesn't price getProfile or the labels.* endpoints since they're
cheap/rare relative to the cap and were never the source of exhaustion;
those costs below are a reasonable approximation, not load-bearing.
"""

import json
import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)

QUOTA_CAP_PER_MINUTE = 6000
QUOTA_WINDOW_SECONDS = 60
WARNING_USAGE_RATIO = 0.5

COST = {
    "messages.list": 5,
    "messages.get": 20,
    "messages.batchModify": 50,
    "getProfile": 1,
    "labels.list": 1,
    "labels.get": 1,
    "labels.create": 5,
    "labels.delete": 5,
}

_RETRYABLE_403_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}


def _http_error_reason(exc: HttpError) -> str:
    """Best-effort extraction of Gmail's error `reason` string from an HttpError."""
    try:
        data = json.loads(exc.content.decode("utf-8"))
        return data.get("error", {}).get("errors", [{}])[0].get("reason", "")
    except Exception:
        return ""


def _is_retryable_http_error(exc: object) -> bool:
    """True for a 429, or a 403 whose reason is actually a rate/quota limit.

    A bare 403 can also mean a genuine permission error (e.g. insufficient
    OAuth scope) — those must not be retried.
    """
    if not isinstance(exc, HttpError):
        return False
    status = getattr(exc.resp, "status", None)
    if status == 429:
        return True
    if status == 403:
        return _http_error_reason(exc) in _RETRYABLE_403_REASONS
    return False


class QuotaTracker:
    """Rolling-window Gmail API quota tracker, gate, and retry helper.

    `clock`/`sleep_fn` are injectable so tests can simulate the passage of
    time without real waits.
    """

    def __init__(
        self,
        cap: int = QUOTA_CAP_PER_MINUTE,
        window_seconds: float = QUOTA_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self._cap = cap
        self._window = window_seconds
        self._clock = clock
        self._sleep = sleep_fn
        self._lock = threading.Lock()
        self._events: deque[tuple[float, int]] = deque()
        self._warned = False

    def _prune_locked(self, now: float) -> None:
        cutoff = now - self._window
        while self._events and self._events[0][0] <= cutoff:
            self._events.popleft()

    def usage(self) -> int:
        """Current rolling-window usage in quota units."""
        with self._lock:
            self._prune_locked(self._clock())
            return sum(cost for _, cost in self._events)

    def _maybe_warn_locked(self, usage_now: int) -> None:
        if not self._warned and usage_now >= self._cap * WARNING_USAGE_RATIO:
            self._warned = True
            logger.warning(
                "Gmail API usage at %d/%d units in the last %ds window (%.0f%% of "
                "the per-minute cap) — possible runaway loop.",
                usage_now,
                self._cap,
                self._window,
                100 * usage_now / self._cap,
            )

    def gate(self, cost: int, status_dict: Optional[dict] = None) -> None:
        """Block until `cost` more units can be spent without exceeding the cap."""
        while True:
            with self._lock:
                now = self._clock()
                self._prune_locked(now)
                current = sum(c for _, c in self._events)
                if current + cost <= self._cap:
                    self._events.append((now, cost))
                    self._maybe_warn_locked(current + cost)
                    return
                oldest_time = self._events[0][0]
                wait = max(oldest_time + self._window - now, 0.1)

            if status_dict is not None:
                status_dict["message"] = (
                    f"Approaching Gmail's rate limit — waiting "
                    f"{int(wait) + 1}s before continuing..."
                )
            self._sleep(wait)

    def execute_with_backoff(
        self,
        request,
        cost: int,
        status_dict: Optional[dict] = None,
        max_retries: int = 5,
    ):
        """gate() then request.execute(), retrying with backoff on a real 429/403."""
        for attempt in range(max_retries + 1):
            self.gate(cost, status_dict)
            try:
                return request.execute()
            except HttpError as e:
                if attempt < max_retries and _is_retryable_http_error(e):
                    wait = min(2**attempt, 30)
                    if status_dict is not None:
                        status_dict["message"] = (
                            f"Gmail rate limit hit — retrying in {wait}s..."
                        )
                    self._sleep(wait)
                    continue
                raise

    def _execute_batch_with_backoff(
        self, batch, status_dict: Optional[dict] = None, max_retries: int = 3
    ) -> None:
        for attempt in range(max_retries + 1):
            try:
                batch.execute()
                return
            except HttpError as e:
                if attempt < max_retries and _is_retryable_http_error(e):
                    wait = min(2**attempt, 30)
                    if status_dict is not None:
                        status_dict["message"] = (
                            f"Gmail rate limit hit — retrying in {wait}s..."
                        )
                    self._sleep(wait)
                    continue
                raise

    def run_batched_gets(
        self,
        service,
        ids: list[str],
        request_factory: Callable[[str], object],
        callback: Callable[[str, Optional[dict], Optional[Exception]], None],
        cost_per_id: int,
        status_dict: Optional[dict] = None,
        batch_size: int = 100,
        max_retry_passes: int = 1,
    ) -> None:
        """Run `request_factory(id)` for every id in `ids` via Gmail batch requests.

        Gates on each chunk's total cost before firing it. A sub-request
        that fails with a rate/quota-shaped error is held back and retried
        (once, by default) after a short backoff instead of being dropped —
        this is what fixes scans silently under-counting senders when a
        batch partially gets rate-limited. Anything still failing after the
        retry passes is reported to `callback` as a terminal exception,
        same contract as today (caller decides how to handle it).
        """
        pending = list(ids)
        exceptions_by_id: dict[str, Exception] = {}

        for pass_num in range(max_retry_passes + 1):
            if not pending:
                return
            retry_ids: list[str] = []

            def batch_callback(request_id, response, exception):
                if exception is not None and _is_retryable_http_error(exception):
                    retry_ids.append(request_id)
                    exceptions_by_id[request_id] = exception
                    return
                callback(request_id, response, exception)

            for i in range(0, len(pending), batch_size):
                chunk = pending[i : i + batch_size]
                self.gate(cost_per_id * len(chunk), status_dict)
                batch = service.new_batch_http_request(callback=batch_callback)
                for msg_id in chunk:
                    batch.add(request_factory(msg_id), request_id=msg_id)
                self._execute_batch_with_backoff(batch, status_dict)

            pending = retry_ids
            if pending and pass_num < max_retry_passes:
                wait = min(2 ** (pass_num + 1), 30)
                if status_dict is not None:
                    status_dict["message"] = (
                        f"Retrying {len(pending)} rate-limited messages in {wait}s..."
                    )
                self._sleep(wait)

        for msg_id in pending:
            callback(msg_id, None, exceptions_by_id.get(msg_id))


# Gmail's 6,000-units/minute cap is tracked per Google account, not per
# process — so each signed-in account (Phase 4a's multi-account switcher)
# needs its own independent rolling window. Sharing one global tracker
# would make switching accounts inherit whatever quota "debt" the
# previously-active account had just run up, blocking a completely fresh
# account for no real reason.
_trackers: dict[str, QuotaTracker] = {}
_trackers_lock = threading.Lock()


def _tracker_for_account(account_key: str) -> QuotaTracker:
    with _trackers_lock:
        if account_key not in _trackers:
            _trackers[account_key] = QuotaTracker()
        return _trackers[account_key]


def _active_tracker() -> QuotaTracker:
    # Deferred import: avoids relying on app/services/__init__.py's
    # exact import ordering to keep this side of the package free of
    # circular-import fragility (same reasoning as auth.py's deferred
    # `from app.services.gmail import quota`).
    from app.services import accounts

    return _tracker_for_account(accounts.get_active_account() or "_unknown")


def gate(cost: int, status_dict: Optional[dict] = None) -> None:
    _active_tracker().gate(cost, status_dict)


def execute_with_backoff(
    request, cost: int, status_dict: Optional[dict] = None, max_retries: int = 5
):
    return _active_tracker().execute_with_backoff(
        request, cost, status_dict, max_retries
    )


def run_batched_gets(
    service,
    ids: list[str],
    request_factory: Callable[[str], object],
    callback: Callable[[str, Optional[dict], Optional[Exception]], None],
    cost_per_id: int,
    status_dict: Optional[dict] = None,
    batch_size: int = 100,
    max_retry_passes: int = 1,
) -> None:
    _active_tracker().run_batched_gets(
        service,
        ids,
        request_factory,
        callback,
        cost_per_id,
        status_dict,
        batch_size,
        max_retry_passes,
    )
