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
from app.services.gmail.quota import QuotaTracker


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
            service, ["m1"], lambda mid: mid, callback, cost_per_id=20, batch_size=10
        )

        assert calls == [("m1", None, second_error)]


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
