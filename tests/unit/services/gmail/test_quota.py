"""
Tests for Gmail API Quota Awareness
-------------------------------------
Rolling-window usage tracking, proactive blocking, and reactive 429/403
backoff (PRD Section 7). Uses QuotaTracker's injectable clock/sleep so
nothing here does a real wait.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from app.services.gmail import quota as quota_module
from app.services.gmail.quota import (
    MAX_CONCURRENT_BATCH_SIZE,
    QuotaTracker,
    estimate_scan_seconds,
)


@pytest.fixture(autouse=True)
def _isolate_quota_trackers():
    """Each test gets a clean per-account tracker registry (module-level
    dict, shared across the whole test session otherwise)."""
    quota_module._trackers.clear()
    yield
    quota_module._trackers.clear()


def _http_error(status: int, reason: str | None = None) -> HttpError:
    resp = MagicMock()
    resp.status = status
    body = {"error": {"errors": [{"reason": reason}]}} if reason else {}
    return HttpError(resp, json.dumps(body).encode("utf-8"))


def _fake_clock_and_sleep(start: float = 0.0):
    """A clock/sleep pair sharing mutable state, so sleep() actually
    advances what the clock reports next (avoiding real waits in gate())."""
    clock_state = {"now": start}

    def clock() -> float:
        return clock_state["now"]

    def sleep(seconds: float) -> None:
        clock_state["now"] += seconds

    return clock, sleep


class TestQuotaTrackerGate:
    def test_usage_under_cap_does_not_block(self):
        clock, sleep = _fake_clock_and_sleep()
        sleep_spy = MagicMock(side_effect=sleep)
        tracker = QuotaTracker(
            cap=100, window_seconds=60, clock=clock, sleep_fn=sleep_spy
        )

        tracker.gate(50)

        sleep_spy.assert_not_called()
        assert tracker.usage() == 50

    def test_usage_exceeding_cap_blocks_until_window_frees_room(self):
        clock, sleep = _fake_clock_and_sleep()
        sleep_spy = MagicMock(side_effect=sleep)
        tracker = QuotaTracker(
            cap=100, window_seconds=60, clock=clock, sleep_fn=sleep_spy
        )
        status = {}

        tracker.gate(60, status)
        tracker.gate(60, status)

        sleep_spy.assert_called_once()
        assert "waiting" in status["message"].lower()
        # The first call's usage has aged out of the window by the time the
        # second call's wait elapses, so only the second charge remains.
        assert tracker.usage() == 60

    def test_crossing_fifty_percent_logs_warning_once(self, caplog):
        clock, sleep = _fake_clock_and_sleep()
        tracker = QuotaTracker(cap=100, window_seconds=60, clock=clock, sleep_fn=sleep)

        with caplog.at_level(logging.WARNING, logger="app.services.gmail.quota"):
            tracker.gate(60)
            tracker.gate(10)

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1


class TestExecuteWithBackoff:
    def test_success_charges_quota_and_returns_response(self):
        clock, sleep = _fake_clock_and_sleep()
        sleep_spy = MagicMock(side_effect=sleep)
        tracker = QuotaTracker(
            cap=1000, window_seconds=60, clock=clock, sleep_fn=sleep_spy
        )
        request = MagicMock()
        request.execute.return_value = {"ok": True}

        result = tracker.execute_with_backoff(request, cost=10)

        assert result == {"ok": True}
        assert tracker.usage() == 10
        sleep_spy.assert_not_called()

    def test_429_retries_then_succeeds(self):
        clock, sleep = _fake_clock_and_sleep()
        sleep_spy = MagicMock(side_effect=sleep)
        tracker = QuotaTracker(
            cap=1000, window_seconds=60, clock=clock, sleep_fn=sleep_spy
        )
        request = MagicMock()
        request.execute.side_effect = [_http_error(429), {"ok": True}]
        status = {}

        result = tracker.execute_with_backoff(request, cost=10, status_dict=status)

        assert result == {"ok": True}
        assert request.execute.call_count == 2
        sleep_spy.assert_called_once()
        assert "retrying" in status["message"].lower()

    def test_transient_5xx_retries_then_succeeds(self):
        clock, sleep = _fake_clock_and_sleep()
        sleep_spy = MagicMock(side_effect=sleep)
        tracker = QuotaTracker(
            cap=1000, window_seconds=60, clock=clock, sleep_fn=sleep_spy
        )
        request = MagicMock()
        request.execute.side_effect = [_http_error(503), {"ok": True}]
        status = {}

        result = tracker.execute_with_backoff(request, cost=10, status_dict=status)

        assert result == {"ok": True}
        assert request.execute.call_count == 2
        sleep_spy.assert_called_once()

    def test_403_with_non_quota_reason_raises_immediately(self):
        clock, sleep = _fake_clock_and_sleep()
        sleep_spy = MagicMock(side_effect=sleep)
        tracker = QuotaTracker(
            cap=1000, window_seconds=60, clock=clock, sleep_fn=sleep_spy
        )
        request = MagicMock()
        request.execute.side_effect = _http_error(403, reason="insufficientPermissions")

        with pytest.raises(HttpError):
            tracker.execute_with_backoff(request, cost=10)

        assert request.execute.call_count == 1
        sleep_spy.assert_not_called()

    def test_retries_exhausted_reraises_original_error(self):
        clock, sleep = _fake_clock_and_sleep()
        sleep_spy = MagicMock(side_effect=sleep)
        tracker = QuotaTracker(
            cap=1000, window_seconds=60, clock=clock, sleep_fn=sleep_spy
        )
        request = MagicMock()
        request.execute.side_effect = _http_error(429)

        with pytest.raises(HttpError):
            tracker.execute_with_backoff(request, cost=10, max_retries=2)

        assert request.execute.call_count == 3
        assert sleep_spy.call_count == 2


class _FakeBatch:
    """Simulates googleapiclient's BatchHttpRequest for run_batched_gets.

    `responses` maps request_id -> a list of ("ok", value) / ("error", exc)
    outcomes, one consumed per execute() attempt that includes that id
    (supports simulating a request that fails once then succeeds on retry).
    """

    def __init__(self, callback, responses):
        self._callback = callback
        self._responses = responses
        self._items: list[tuple[str, object]] = []

    def add(self, request, request_id=None):
        self._items.append((request_id, request))

    def execute(self):
        items, self._items = self._items, []
        for request_id, _request in items:
            kind, value = self._responses[request_id].pop(0)
            if kind == "ok":
                self._callback(request_id, value, None)
            else:
                self._callback(request_id, None, value)


def _fake_batch_service(responses: dict) -> MagicMock:
    service = MagicMock()
    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(
        callback, responses
    )
    return service


class TestRunBatchedGets:
    def test_normal_chunking_and_callback_behavior_unchanged(self):
        clock, sleep = _fake_clock_and_sleep()
        tracker = QuotaTracker(
            cap=10_000, window_seconds=60, clock=clock, sleep_fn=sleep
        )
        responses = {
            "a": [("ok", {"id": "a"})],
            "b": [("ok", {"id": "b"})],
            "c": [("ok", {"id": "c"})],
        }
        service = _fake_batch_service(responses)
        received: dict[str, tuple] = {}

        def callback(request_id, response, exception):
            received[request_id] = (response, exception)

        tracker.run_batched_gets(
            service,
            ["a", "b", "c"],
            lambda mid: mid,
            callback,
            cost_per_id=20,
            batch_size=2,
        )

        assert received == {
            "a": ({"id": "a"}, None),
            "b": ({"id": "b"}, None),
            "c": ({"id": "c"}, None),
        }
        assert tracker.usage() == 60

    def test_quota_error_is_retried_once_and_succeeds(self):
        clock, sleep = _fake_clock_and_sleep()
        sleep_spy = MagicMock(side_effect=sleep)
        tracker = QuotaTracker(
            cap=10_000, window_seconds=60, clock=clock, sleep_fn=sleep_spy
        )
        responses = {
            "m1": [("error", _http_error(429)), ("ok", {"id": "m1"})],
            "m2": [("ok", {"id": "m2"})],
        }
        service = _fake_batch_service(responses)
        received: dict[str, tuple] = {}

        def callback(request_id, response, exception):
            received[request_id] = (response, exception)

        tracker.run_batched_gets(
            service,
            ["m1", "m2"],
            lambda mid: mid,
            callback,
            cost_per_id=20,
            batch_size=10,
        )

        assert received["m2"] == ({"id": "m2"}, None)
        assert received["m1"] == ({"id": "m1"}, None)
        sleep_spy.assert_called()

    def test_still_failing_after_retry_pass_reaches_callback_as_terminal_exception(
        self,
    ):
        clock, sleep = _fake_clock_and_sleep()
        tracker = QuotaTracker(
            cap=10_000, window_seconds=60, clock=clock, sleep_fn=sleep
        )
        second_error = _http_error(429)
        responses = {"m1": [("error", _http_error(429)), ("error", second_error)]}
        service = _fake_batch_service(responses)
        calls: list[tuple] = []

        def callback(request_id, response, exception):
            calls.append((request_id, response, exception))

        tracker.run_batched_gets(
            service,
            ["m1"],
            lambda mid: mid,
            callback,
            cost_per_id=20,
            batch_size=10,
            max_retry_passes=1,
        )

        assert calls == [("m1", None, second_error)]

    def test_default_retries_twice_before_giving_up(self):
        """Manual testing showed even a well-sized batch can occasionally
        collide with concurrent activity from outside this app (another
        device/tab sharing the same account's concurrent-request budget) —
        one retry pass wasn't always enough margin, so the default is 2."""
        clock, sleep = _fake_clock_and_sleep()
        tracker = QuotaTracker(
            cap=10_000, window_seconds=60, clock=clock, sleep_fn=sleep
        )
        responses = {
            "m1": [
                ("error", _http_error(429)),
                ("error", _http_error(429)),
                ("ok", {"id": "m1"}),
            ]
        }
        service = _fake_batch_service(responses)
        received: dict[str, tuple] = {}

        def callback(request_id, response, exception):
            received[request_id] = (response, exception)

        tracker.run_batched_gets(
            service, ["m1"], lambda mid: mid, callback, cost_per_id=20, batch_size=10
        )

        assert received["m1"] == ({"id": "m1"}, None)

    def test_logs_summary_line_with_requested_succeeded_failed_counts(self, caplog):
        """Manual testing needed to hand-count individual chunk-fire trace
        lines to check for a mismatch — this one authoritative line makes
        that unnecessary.

        _trace_logger has propagate=False by design (self-contained, not
        duplicated by any other handler an app might configure), so caplog
        must be attached to it directly rather than relying on the usual
        root-logger propagation `at_level` depends on.
        """
        clock, sleep = _fake_clock_and_sleep()
        tracker = QuotaTracker(
            cap=10_000, window_seconds=60, clock=clock, sleep_fn=sleep
        )
        responses = {
            "a": [("ok", {"id": "a"})],
            "b": [("error", _http_error(403, reason="insufficientPermissions"))],
        }
        service = _fake_batch_service(responses)

        trace_logger = quota_module._trace_logger
        previous_level = trace_logger.level
        trace_logger.addHandler(caplog.handler)
        trace_logger.setLevel(logging.INFO)
        try:
            tracker.run_batched_gets(
                service,
                ["a", "b"],
                lambda mid: mid,
                lambda *args: None,
                cost_per_id=10,
                batch_size=10,
            )
        finally:
            trace_logger.removeHandler(caplog.handler)
            trace_logger.setLevel(previous_level)

        summaries = [
            r.getMessage() for r in caplog.records if "summary" in r.getMessage()
        ]
        assert len(summaries) == 1
        assert "requested=2" in summaries[0]
        assert "succeeded=1" in summaries[0]
        assert "failed=1" in summaries[0]


class TestPerAccountTrackerIsolation:
    """Gmail's 6,000/min cap is tracked per Google account, not per process
    (Phase 4a's multi-account switcher) — each active account must get its
    own independent rolling window, not share one global bucket."""

    def test_different_active_accounts_get_independent_trackers(self):
        with patch(
            "app.services.accounts.get_active_account", return_value="a@example.com"
        ):
            quota_module.gate(3000)
        with patch(
            "app.services.accounts.get_active_account", return_value="b@example.com"
        ):
            quota_module.gate(3000)

        assert quota_module._tracker_for_account("a@example.com").usage() == 3000
        assert quota_module._tracker_for_account("b@example.com").usage() == 3000

    def test_switching_active_account_does_not_inherit_previous_usage(self):
        with patch(
            "app.services.accounts.get_active_account", return_value="a@example.com"
        ):
            # Push account "a" close to its own cap.
            quota_module.gate(5900)

        with patch(
            "app.services.accounts.get_active_account", return_value="b@example.com"
        ):
            # A freshly-switched-to account must not inherit "a"'s usage —
            # this charge must not block even though "a" is near its cap.
            quota_module.gate(100)

        assert quota_module._tracker_for_account("a@example.com").usage() == 5900
        assert quota_module._tracker_for_account("b@example.com").usage() == 100


class TestRunBatchedGetsConcurrencyClamp:
    """Gmail caps concurrent requests per user at 50 (confirmed via a real
    "Too many concurrent requests for user" 429 during manual testing) —
    independent of the 6,000-units/minute budget. A batch's sub-requests
    fire essentially simultaneously, so batch_size must never exceed that,
    regardless of what a caller asks for."""

    def test_batch_size_is_clamped_even_when_caller_asks_for_more(self):
        clock, sleep = _fake_clock_and_sleep()
        tracker = QuotaTracker(
            cap=1_000_000, window_seconds=60, clock=clock, sleep_fn=sleep
        )
        ids = [f"m{i}" for i in range(120)]
        chunk_sizes: list[int] = []

        class _SizeTrackingBatch:
            def __init__(self, callback):
                self._callback = callback
                self._items: list[tuple[str, object]] = []

            def add(self, request, request_id=None):
                self._items.append((request_id, request))

            def execute(self):
                chunk_sizes.append(len(self._items))
                for request_id, _request in self._items:
                    self._callback(request_id, {"id": request_id}, None)

        service = MagicMock()
        service.new_batch_http_request.side_effect = (
            lambda callback: _SizeTrackingBatch(callback)
        )
        received: dict[str, object] = {}

        tracker.run_batched_gets(
            service,
            ids,
            lambda mid: mid,
            lambda rid, resp, exc: received.__setitem__(rid, resp),
            cost_per_id=1,
            batch_size=100,  # caller asks for more than the concurrency ceiling
        )

        assert all(size <= MAX_CONCURRENT_BATCH_SIZE for size in chunk_sizes)
        assert len(received) == 120


class TestEstimateScanSeconds:
    def test_zero_or_negative_returns_zero(self):
        assert estimate_scan_seconds(0) == 0
        assert estimate_scan_seconds(-5) == 0

    def test_small_scan_fits_in_one_window_returns_zero(self):
        # 100 messages: 100*20 + 1*5 = 2005 units, well under the 6,000 cap.
        assert estimate_scan_seconds(100) == 0

    def test_large_scan_matches_confirmed_real_world_behavior(self):
        # 992 messages: matches the actual scan from manual testing (real
        # elapsed time was ~188.5s) - 992*20 + 2*5 = 19850 units,
        # extra_windows = ceil((19850-6000)/6000) = 3 -> 3*60+15 = 195s.
        assert estimate_scan_seconds(992) == 195

    def test_estimate_scales_with_message_count(self):
        assert estimate_scan_seconds(2000) > estimate_scan_seconds(1000) > 0

    def test_exactly_at_cap_returns_zero(self):
        # 300 messages * 20 = 6000 units exactly (ignoring list() cost),
        # comfortably under with the small list() addition too.
        assert estimate_scan_seconds(299) == 0
