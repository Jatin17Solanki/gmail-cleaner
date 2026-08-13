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
import math
import threading
import time
from collections import deque
from typing import Callable, Optional

from googleapiclient.errors import HttpError

from app.core import settings

logger = logging.getLogger(__name__)

# Dedicated, self-contained trace logger for QUOTA_TRACE_LOGGING (opt-in
# debugging aid — see app/core/config.py). The app has no logging.basicConfig
# anywhere, so a plain logger.info() call here would silently vanish (Python's
# fallback handler only shows WARNING+). This logger gets its own handler and
# formatter instead, so it works regardless of whatever logging setup (or
# lack of one) the rest of the app has, and always includes a real timestamp.
_trace_logger = logging.getLogger(f"{__name__}.trace")
_trace_logger.propagate = False
if settings.quota_trace_logging:
    if not _trace_logger.handlers:
        _trace_handler = logging.StreamHandler()
        _trace_handler.setFormatter(
            logging.Formatter("%(asctime)s [quota] %(message)s")
        )
        _trace_logger.addHandler(_trace_handler)
    _trace_logger.setLevel(logging.INFO)
else:
    _trace_logger.setLevel(logging.WARNING)  # .info() calls become cheap no-ops

QUOTA_CAP_PER_MINUTE = 6000
QUOTA_WINDOW_SECONDS = 60
WARNING_USAGE_RATIO = 0.5

# Gmail enforces a *separate* limit from the 6,000-units/minute budget:
# max 50 concurrent requests per user (confirmed via Google's own
# error-handling docs). Crucially, this is per *account*, not per
# application — anything else concurrently touching the same mailbox
# (another device, another browser tab, a phone app syncing in the
# background) shares the same budget, invisibly to us. Manual testing
# confirmed batch_size=50 (exactly at the documented ceiling) still
# occasionally trips "Too many concurrent requests for user" — so this
# stays well under 50 to leave headroom for concurrent activity we can't
# see or control, not just because Google's stated number is 50. Enforced
# as a hard clamp in run_batched_gets, not just a caller convention, since
# this is exactly the kind of constraint a "quota awareness" module should
# own.
MAX_CONCURRENT_BATCH_SIZE = 25

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

# Small, fixed cushion added on top of the theoretical minimum below, for
# real-world overhead (network round-trips, retries, other small charges
# like getProfile) the formula doesn't model. Empirically, real scans in
# this range landed within ~5% of the unpadded formula - see PROGRESS.md's
# Phase 4a2 investigation - so this is a safety margin, not a correction
# for a known systematic error.
_ESTIMATE_BUFFER_SECONDS = 15


def _extra_wait_seconds(total_cost: int) -> int:
    """Shared cost-to-wall-clock conversion behind estimate_scan_seconds()
    and estimate_sender_totals_seconds() below - same window math gate()
    itself enforces."""
    if total_cost <= QUOTA_CAP_PER_MINUTE:
        return 0  # fits in a single window - no proactive wait expected
    extra_windows = math.ceil(
        (total_cost - QUOTA_CAP_PER_MINUTE) / QUOTA_CAP_PER_MINUTE
    )
    return extra_windows * QUOTA_WINDOW_SECONDS + _ESTIMATE_BUFFER_SECONDS


def estimate_scan_seconds(message_count: int) -> int:
    """Rough wall-clock estimate for scanning `message_count` messages.

    Uses the same cost model gate()/COST already enforce, so this tracks
    real behavior rather than being a separate guess. Assumes a fresh quota
    budget for the active account — concurrent activity elsewhere on the
    same account (another tab, a routine, another device) can make the
    real scan take longer than this estimate, same caveat as
    MAX_CONCURRENT_BATCH_SIZE above.

    Deliberately doesn't include fetch_true_sender_totals()'s own cost -
    that phase's sender count isn't known until after this estimate is
    first shown (sender grouping only happens once every message's
    metadata has been fetched). See estimate_sender_totals_seconds() for
    the follow-up estimate scan_senders_for_delete()/_archive()/_markread()
    add once the true sender count is known.
    """
    if message_count <= 0:
        return 0
    list_calls = math.ceil(message_count / 500)
    total_cost = (
        message_count * COST["messages.get"] + list_calls * COST["messages.list"]
    )
    return _extra_wait_seconds(total_cost)


def estimate_sender_totals_seconds(sender_count: int) -> int:
    """Additional wall-clock estimate for fetch_true_sender_totals()'s own
    pass - one messages.list() call per unique sender (5 units flat each,
    ignoring the rare case of a single sender needing multiple pages, same
    approximation-not-load-bearing standard as estimate_scan_seconds()).

    Meant to be added on top of whatever estimate_scan_seconds() already
    produced, not used standalone - the two phases run back to back within
    the same scan, so their wait time is cumulative. Errs toward
    overestimating: it doesn't know how much headroom the current quota
    window already has left over from the scan phase, so it prices this
    phase as if starting from a fresh window. Better to overestimate a
    wait than promise one that runs short.
    """
    if sender_count <= 0:
        return 0
    return _extra_wait_seconds(sender_count * COST["messages.list"])


_RETRYABLE_403_REASONS = {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}
# Gmail's own backend can return transient 5xx errors under load, independent
# of any per-user quota - standard practice (per Google's own API client
# guidance) is to back off and retry these too, not just 429/quota-403.
_RETRYABLE_5XX_STATUSES = {500, 502, 503, 504}


def _http_error_reason(exc: HttpError) -> str:
    """Best-effort extraction of Gmail's error `reason` string from an HttpError."""
    try:
        data = json.loads(exc.content.decode("utf-8"))
        return data.get("error", {}).get("errors", [{}])[0].get("reason", "")
    except Exception:
        return ""


def _is_retryable_http_error(exc: object) -> bool:
    """True for a 429, a 403 whose reason is actually a rate/quota limit, or
    a transient 5xx server error.

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
    return status in _RETRYABLE_5XX_STATUSES


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
        account_key: str = "",
    ) -> None:
        self._cap = cap
        self._window = window_seconds
        self._clock = clock
        self._sleep = sleep_fn
        self._account_key = account_key
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

    def gate(
        self, cost: int, status_dict: Optional[dict] = None, label: str = ""
    ) -> None:
        """Block until `cost` more units can be spent without exceeding the cap."""
        while True:
            with self._lock:
                now = self._clock()
                self._prune_locked(now)
                current = sum(c for _, c in self._events)
                if current + cost <= self._cap:
                    self._events.append((now, cost))
                    self._maybe_warn_locked(current + cost)
                    _trace_logger.info(
                        "account=%s %scost=%d usage=%d/%d",
                        self._account_key or "unknown",
                        f"{label} " if label else "",
                        cost,
                        current + cost,
                        self._cap,
                    )
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
        label: str = "",
    ):
        """gate() then request.execute(), retrying with backoff on a real 429/403."""
        for attempt in range(max_retries + 1):
            self.gate(cost, status_dict, label)
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
        batch_size: int = MAX_CONCURRENT_BATCH_SIZE,
        max_retry_passes: int = 2,
        label: str = "",
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
        # Hard clamp, not just a documented convention - a batch's
        # sub-requests fire essentially simultaneously, so anything above
        # Gmail's real 50-concurrent-requests-per-user ceiling risks the
        # exact "Too many concurrent requests for user" 429s this method
        # exists to handle gracefully. Applies to retry passes too, since
        # they reuse this same batch_size.
        batch_size = min(batch_size, MAX_CONCURRENT_BATCH_SIZE)
        total_requested = len(ids)
        pending = list(ids)
        exceptions_by_id: dict[str, Exception] = {}
        permanent_failures = 0

        for pass_num in range(max_retry_passes + 1):
            if not pending:
                break
            retry_ids: list[str] = []

            def batch_callback(request_id, response, exception):
                nonlocal permanent_failures
                if exception is not None:
                    if _is_retryable_http_error(exception):
                        retry_ids.append(request_id)
                        exceptions_by_id[request_id] = exception
                        return
                    permanent_failures += 1
                    logger.warning(
                        "Gmail request for message %s failed (non-retryable): %r",
                        request_id,
                        exception,
                    )
                callback(request_id, response, exception)

            for i in range(0, len(pending), batch_size):
                chunk = pending[i : i + batch_size]
                self.gate(
                    cost_per_id * len(chunk),
                    status_dict,
                    f"{label} x{len(chunk)}" if label else f"x{len(chunk)}",
                )
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
            permanent_failures += 1
            exc = exceptions_by_id.get(msg_id)
            logger.warning(
                "Gmail request for message %s still failing after %d retry pass(es): %r",
                msg_id,
                max_retry_passes,
                exc,
            )
            callback(msg_id, None, exc)

        # One authoritative summary line per run — requested vs. succeeded
        # vs. permanently failed (both the immediate-non-retryable and the
        # exhausted-retries paths) — so a mismatch can be confirmed or
        # ruled out without hand-counting individual chunk-fire trace lines.
        _trace_logger.info(
            "run_batched_gets summary: requested=%d succeeded=%d failed=%d%s",
            total_requested,
            total_requested - permanent_failures,
            permanent_failures,
            f" ({label})" if label else "",
        )


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
            _trackers[account_key] = QuotaTracker(account_key=account_key)
        return _trackers[account_key]


def _active_tracker() -> QuotaTracker:
    # Deferred import: avoids relying on app/services/__init__.py's
    # exact import ordering to keep this side of the package free of
    # circular-import fragility (same reasoning as auth.py's deferred
    # `from app.services.gmail import quota`).
    from app.services import accounts

    return _tracker_for_account(accounts.get_active_account() or "_unknown")


def gate(cost: int, status_dict: Optional[dict] = None, label: str = "") -> None:
    _active_tracker().gate(cost, status_dict, label)


def execute_with_backoff(
    request,
    cost: int,
    status_dict: Optional[dict] = None,
    max_retries: int = 5,
    label: str = "",
):
    return _active_tracker().execute_with_backoff(
        request, cost, status_dict, max_retries, label
    )


def run_batched_gets(
    service,
    ids: list[str],
    request_factory: Callable[[str], object],
    callback: Callable[[str, Optional[dict], Optional[Exception]], None],
    cost_per_id: int,
    status_dict: Optional[dict] = None,
    batch_size: int = MAX_CONCURRENT_BATCH_SIZE,
    max_retry_passes: int = 2,
    label: str = "",
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
        label,
    )


def fetch_true_sender_totals(
    service,
    senders: list[dict],
    filters: Optional[dict],
    status_dict: Optional[dict] = None,
    label: str = "messages.list (true total)",
) -> None:
    """Fill in `total_count` on each sender dict, in place, with an *exact*
    count of messages matching that sender + the active filters.

    A scan's own `count` field only reflects how many of a sender's messages
    fell within the scanned window (limited by the scan's own `limit`) - it
    can badly understate a sender's true mail volume, which is misleading
    right where a user decides whether to delete/archive/mark a sender.

    Counts by paginating messages.list() to exhaustion - the same pattern
    the actual bulk-action functions already use - rather than reading
    Gmail's resultSizeEstimate field. That field is documented by Google as
    an *estimate*, not an exact count, and was found in real use to inflate
    badly enough to be actively untrustworthy (a real repro during review:
    37 senders summed to a "total" of 7,437 emails, exceeding the account's
    actual inbox size). messages.list is a flat 5 units per call regardless
    of how many results a page returns, so this costs the same as the
    estimate approach for any sender under 500 matching messages (the
    common case) - it only scales up for senders with genuinely large mail
    volumes, which is exactly where an accurate number matters most anyway.

    A per-sender failure falls back to that sender's already-known sampled
    count rather than aborting the scan - the UI still works, just without
    the corrected number for that one sender.
    """
    from app.services.gmail.helpers import build_gmail_query

    for sender_data in senders:
        try:
            query_filters = dict(filters or {})
            query_filters["sender"] = sender_data["email"]
            query = build_gmail_query(query_filters)
            total = 0
            page_token = None
            while True:
                result = execute_with_backoff(
                    service.users()
                    .messages()
                    .list(
                        userId="me", q=query, maxResults=500, pageToken=page_token
                    ),
                    COST["messages.list"],
                    status_dict,
                    label=label,
                )
                total += len(result.get("messages", []))
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
            sender_data["total_count"] = total
        except Exception:
            sender_data["total_count"] = sender_data["count"]
