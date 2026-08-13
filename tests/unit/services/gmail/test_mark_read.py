"""
Tests for Gmail Mark-as-Read Operations
------------------------------------------
Phase 2 added operation-log coverage: a successful mark-read must be
restorable. Phase 3 replaces the old blind "mark N most recent unread"
count-based flow with a sender-row list (mirroring Delete/Archive) - own
scan (scan_senders_for_markread) and a senders-scoped bulk action
(mark_emails_as_read_bulk_background), routed through build_gmail_query
with the active filters (#107 pattern).
"""

from unittest.mock import MagicMock, patch

from app.core import state
from app.services import operation_log
from app.services.gmail.mark_read import (
    mark_emails_as_read_bulk_background,
    scan_senders_for_markread,
)


def _mock_service(list_results=None):
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        list_results if list_results is not None else {"messages": []}
    )
    return service


def _mock_batch_service(message_ids, responses_by_id):
    """Build a MagicMock Gmail service whose messages().list() returns
    message_ids, and whose batch API feeds responses_by_id[msg_id] into
    scan_senders_for_markread's process_message callback."""
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = {
        "messages": [{"id": mid} for mid in message_ids]
    }
    service.users.return_value.messages.return_value.get.side_effect = (
        lambda userId, id, format, metadataHeaders: responses_by_id[id]
    )

    class _FakeBatch:
        def __init__(self, callback):
            self._callback = callback
            self._requests = []

        def add(self, request, request_id=None):
            self._requests.append((request_id, request))

        def execute(self):
            for i, (request_id, response) in enumerate(self._requests):
                self._callback(request_id or str(i), response, None)

    service.new_batch_http_request.side_effect = lambda callback: _FakeBatch(callback)
    return service


def _message_response(sender_email, subject):
    return {
        "id": f"msg-{subject}",
        "sizeEstimate": 100,
        "payload": {
            "headers": [
                {"name": "From", "value": f"Sender <{sender_email}>"},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Wed, 15 Nov 2025 10:30:00 +0000"},
            ]
        },
    }


class TestMarkReadBulkWritesOperationLog:
    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_mark_read_logs_entry(self, mock_get_service):
        service = _mock_service({"messages": [{"id": "m1"}, {"id": "m2"}]})
        mock_get_service.return_value = (service, None)

        mark_emails_as_read_bulk_background(["newsletter@example.com"])

        entries = operation_log.list_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action_type"] == "mark_read"
        assert set(entry["message_ids"]) == {"m1", "m2"}
        assert entry["added_labels"] == []
        assert entry["removed_labels"] == ["UNREAD"]
        assert entry["summary"]["senders"] == ["newsletter@example.com"]

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_no_unread_messages_does_not_log(self, mock_get_service):
        service = _mock_service({"messages": []})
        mock_get_service.return_value = (service, None)

        mark_emails_as_read_bulk_background(["newsletter@example.com"])

        assert operation_log.list_entries() == []


class TestMarkReadBulkQueryScoping:
    """#107 pattern: marking read must only affect the filtered subset of
    unread messages the user actually reviewed."""

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_scoped_to_sender_unread_and_inbox_by_default(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        mark_emails_as_read_bulk_background(["newsletter@example.com"])

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "is:unread" in query
        assert "label:INBOX" in query

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_scoped_to_sender_and_active_filters(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        mark_emails_as_read_bulk_background(
            ["newsletter@example.com"], {"category": "promotions"}
        )

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "category:promotions" in query
        assert "is:unread" in query

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_removes_marked_senders_from_cached_scan_results(self, mock_get_service):
        state.markread_scan_results = [
            {"email": "newsletter@example.com", "count": 3},
            {"email": "keep@example.com", "count": 1},
        ]
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        mark_emails_as_read_bulk_background(["newsletter@example.com"])

        remaining = [r["email"] for r in state.markread_scan_results]
        assert remaining == ["keep@example.com"]


class TestMarkReadBulkExclusion:
    """Phase 4c: per-message checkboxes exclude specific messages from an
    otherwise sender-wide mark-as-read - query minus excluded, not an
    include-list (see delete.py's delete_emails_bulk_background for why)."""

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_excluded_message_id_is_not_marked(self, mock_get_service):
        service = _mock_service(
            {"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}
        )
        mock_get_service.return_value = (service, None)

        mark_emails_as_read_bulk_background(
            ["newsletter@example.com"], excluded_message_ids=["m2"]
        )

        batch_modify = service.users.return_value.messages.return_value.batchModify
        body = batch_modify.call_args.kwargs["body"]
        assert set(body["ids"]) == {"m1", "m3"}

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_excluding_every_matched_message_marks_nothing(self, mock_get_service):
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        mark_emails_as_read_bulk_background(
            ["newsletter@example.com"], excluded_message_ids=["m1"]
        )

        service.users.return_value.messages.return_value.batchModify.assert_not_called()
        assert operation_log.list_entries() == []


class TestScanSendersForMarkreadTrueTotals:
    """Phase 4c follow-up: a sender's `count` only reflects the scanned
    window - the scan also fetches the real, exact total (by paginating
    messages.list() to exhaustion, not Gmail's unreliable
    resultSizeEstimate field, scoped to unread mail same as the scan
    itself) so the UI isn't showing a number that understates what
    mark-as-read would actually affect."""

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_scan_result_includes_total_count(self, mock_get_service):
        response = _message_response("digest@example.com", "Daily digest")
        service = _mock_batch_service(["m1"], {"m1": response})
        mock_get_service.return_value = (service, None)
        service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "m1"}]},
            {"messages": [{"id": f"m{i}"} for i in range(24)]},
        ]

        scan_senders_for_markread(limit=10)

        sender = state.markread_scan_results[0]
        assert sender["count"] == 1
        assert sender["total_count"] == 24

    @patch("app.services.gmail.mark_read.quota.estimate_sender_totals_seconds")
    @patch("app.services.gmail.mark_read.quota.estimate_scan_seconds")
    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_estimated_seconds_is_topped_up_after_sender_grouping(
        self, mock_get_service, mock_estimate_scan, mock_estimate_totals
    ):
        """The upfront estimate (set right after the initial
        messages.list()) doesn't know the true sender count yet - that's
        only known after grouping. It must get topped up with
        fetch_true_sender_totals()'s own cost once that count is known."""
        mock_estimate_scan.return_value = 40
        mock_estimate_totals.return_value = 75
        response = _message_response("digest@example.com", "Daily digest")
        service = _mock_batch_service(["m1"], {"m1": response})
        mock_get_service.return_value = (service, None)
        service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "m1"}]},
            {"messages": [{"id": "m1"}]},
        ]

        scan_senders_for_markread(limit=10)

        mock_estimate_totals.assert_called_once_with(1)
        assert state.markread_scan_status["estimated_seconds"] == 115

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_true_total_query_is_scoped_to_unread(self, mock_get_service):
        """The true-count pass must reuse query_filters (unread_only=True),
        not the raw filters - otherwise it would count all mail from the
        sender, not just unread, which is what this view actually acts on."""
        response = _message_response("digest@example.com", "Daily digest")
        service = _mock_batch_service(["m1"], {"m1": response})
        mock_get_service.return_value = (service, None)
        service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "m1"}]},
            {"messages": [{"id": f"m{i}"} for i in range(24)]},
        ]

        scan_senders_for_markread(limit=10)

        list_call = service.users.return_value.messages.return_value.list
        total_count_query = list_call.call_args_list[-1].kwargs["q"]
        assert "is:unread" in total_count_query
        assert "from:digest@example.com" in total_count_query


class TestScanSendersForMarkread:
    """Phase 3: Mark-as-read gets its own sender-row list scan, mirroring
    scan_senders_for_delete/scan_senders_for_archive, always scoped to
    unread mail."""

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_scan_groups_by_sender(self, mock_get_service):
        responses = {
            "m1": _message_response("digest@example.com", "Daily digest 1"),
            "m2": _message_response("digest@example.com", "Daily digest 2"),
        }
        service = _mock_batch_service(["m1", "m2"], responses)
        mock_get_service.return_value = (service, None)

        scan_senders_for_markread(limit=10)

        assert len(state.markread_scan_results) == 1
        sender = state.markread_scan_results[0]
        assert sender["email"] == "digest@example.com"
        assert sender["count"] == 2

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_scan_query_is_always_scoped_to_unread(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        scan_senders_for_markread(limit=10, filters={"category": "promotions"})

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "is:unread" in query
        assert "category:promotions" in query

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_scan_stores_active_filters_in_state(self, mock_get_service):
        mock_get_service.return_value = (None, "Not authenticated")

        scan_senders_for_markread(limit=10, filters={"category": "promotions"})

        assert state.markread_scan_filters == {"category": "promotions"}

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_scan_no_results_reports_done(self, mock_get_service):
        service = _mock_service({"messages": []})
        mock_get_service.return_value = (service, None)

        scan_senders_for_markread(limit=10)

        assert state.markread_scan_status["done"] is True
        assert state.markread_scan_results == []

    def test_invalid_limit_reports_error(self):
        scan_senders_for_markread(limit=0)

        assert state.markread_scan_status["error"] == "Limit must be greater than 0"
        assert state.markread_scan_status["done"] is True
