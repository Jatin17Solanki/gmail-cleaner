"""
Tests for Gmail Archive Operations
------------------------------------
Phase 2 added operation-log coverage: a successful archive must be
restorable. Phase 3 gives Archive its own scan (previously it only operated
on whatever the Delete tab had already scanned) and routes the archive
query through build_gmail_query with the active scan's filters, same #107
pattern already used by delete/label — Archive was the one op still using a
bare f-string query with no filters param.
"""

from unittest.mock import MagicMock, patch

from app.core import state
from app.services import operation_log
from app.services.gmail.archive import (
    archive_emails_background,
    scan_senders_for_archive,
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
    scan_senders_for_archive's process_message callback."""
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


class TestArchiveWritesOperationLog:
    @patch("app.services.gmail.archive.get_gmail_service")
    def test_archive_logs_entry(self, mock_get_service):
        service = _mock_service({"messages": [{"id": "m1"}, {"id": "m2"}]})
        mock_get_service.return_value = (service, None)

        archive_emails_background(["a@example.com"])

        entries = operation_log.list_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action_type"] == "archive"
        assert set(entry["message_ids"]) == {"m1", "m2"}
        assert entry["added_labels"] == []
        assert entry["removed_labels"] == ["INBOX"]
        assert entry["summary"]["senders"] == ["a@example.com"]

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_no_matching_messages_does_not_log(self, mock_get_service):
        service = _mock_service({"messages": []})
        mock_get_service.return_value = (service, None)

        archive_emails_background(["a@example.com"])

        assert operation_log.list_entries() == []


class TestArchiveEmailsQueryScoping:
    """Phase 3, #107 pattern: archive must only affect the filtered subset
    of messages the user actually reviewed, not every inbox message from
    that sender - archive_emails_background previously used a bare
    f"from:{sender} in:inbox" string with no filters param at all."""

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_archive_scoped_to_sender_and_inbox_by_default(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        archive_emails_background(["newsletter@example.com"])

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "label:INBOX" in query

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_archive_scoped_to_sender_and_active_filters(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        archive_emails_background(["newsletter@example.com"], {"older_than": "180d"})

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "older_than:180d" in query

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_archive_removes_archived_senders_from_cached_scan_results(
        self, mock_get_service
    ):
        state.archive_scan_results = [
            {"email": "newsletter@example.com", "count": 3},
            {"email": "keep@example.com", "count": 1},
        ]
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        archive_emails_background(["newsletter@example.com"])

        remaining = [r["email"] for r in state.archive_scan_results]
        assert remaining == ["keep@example.com"]


class TestArchiveEmailsExclusion:
    """Phase 4c: per-message checkboxes exclude specific messages from an
    otherwise sender-wide archive - query minus excluded, not an
    include-list (see delete.py's delete_emails_bulk_background for why)."""

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_excluded_message_id_is_not_archived(self, mock_get_service):
        service = _mock_service(
            {"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}
        )
        mock_get_service.return_value = (service, None)

        archive_emails_background(
            ["newsletter@example.com"], excluded_message_ids=["m2"]
        )

        batch_modify = service.users.return_value.messages.return_value.batchModify
        body = batch_modify.call_args.kwargs["body"]
        assert set(body["ids"]) == {"m1", "m3"}

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_excluding_every_matched_message_archives_nothing(
        self, mock_get_service
    ):
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        archive_emails_background(
            ["newsletter@example.com"], excluded_message_ids=["m1"]
        )

        service.users.return_value.messages.return_value.batchModify.assert_not_called()
        assert operation_log.list_entries() == []


class TestScanSendersForArchiveTrueTotals:
    """Phase 4c follow-up: a sender's `count` only reflects the scanned
    window - the scan also fetches the real, exact total (by paginating
    messages.list() to exhaustion, not Gmail's unreliable
    resultSizeEstimate field) so the UI isn't showing a number that
    understates what an archive would actually affect."""

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_scan_result_includes_total_count(self, mock_get_service):
        response = _message_response("promo@example.com", "Sale")
        service = _mock_batch_service(["m1"], {"m1": response})
        mock_get_service.return_value = (service, None)
        service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "m1"}]},
            {"messages": [{"id": f"m{i}"} for i in range(87)]},
        ]

        scan_senders_for_archive(limit=10)

        sender = state.archive_scan_results[0]
        assert sender["count"] == 1
        assert sender["total_count"] == 87


class TestScanSendersForArchiveEstimatedSeconds:
    """The upfront estimate (set right after the initial messages.list())
    doesn't know the true sender count yet - that's only known after
    grouping. It must get topped up with fetch_true_sender_totals()'s own
    cost once that count is known, not left understating the real wait."""

    @patch("app.services.gmail.archive.quota.estimate_sender_totals_seconds")
    @patch("app.services.gmail.archive.quota.estimate_scan_seconds")
    @patch("app.services.gmail.archive.get_gmail_service")
    def test_estimated_seconds_is_topped_up_after_sender_grouping(
        self, mock_get_service, mock_estimate_scan, mock_estimate_totals
    ):
        mock_estimate_scan.return_value = 40
        mock_estimate_totals.return_value = 75
        response = _message_response("promo@example.com", "Sale")
        service = _mock_batch_service(["m1"], {"m1": response})
        mock_get_service.return_value = (service, None)
        service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "m1"}]},
            {"messages": [{"id": "m1"}]},
        ]

        scan_senders_for_archive(limit=10)

        mock_estimate_totals.assert_called_once_with(1)
        assert state.archive_scan_status["estimated_seconds"] == 115


class TestScanSendersForArchive:
    """Phase 3: Archive gets its own scan, mirroring scan_senders_for_delete
    (sender/count/subjects/dates/message_ids), independent of Delete's
    cached results."""

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_scan_groups_by_sender(self, mock_get_service):
        responses = {
            "m1": _message_response("promo@example.com", "Sale 1"),
            "m2": _message_response("promo@example.com", "Sale 2"),
        }
        service = _mock_batch_service(["m1", "m2"], responses)
        mock_get_service.return_value = (service, None)

        scan_senders_for_archive(limit=10)

        assert len(state.archive_scan_results) == 1
        sender = state.archive_scan_results[0]
        assert sender["email"] == "promo@example.com"
        assert sender["count"] == 2
        assert set(sender["message_ids"]) == {"msg-Sale 1", "msg-Sale 2"}

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_scan_stores_active_filters_in_state(self, mock_get_service):
        mock_get_service.return_value = (None, "Not authenticated")

        scan_senders_for_archive(limit=10, filters={"older_than": "180d"})

        assert state.archive_scan_filters == {"older_than": "180d"}

    @patch("app.services.gmail.archive.get_gmail_service")
    def test_scan_no_results_reports_done(self, mock_get_service):
        service = _mock_service({"messages": []})
        mock_get_service.return_value = (service, None)

        scan_senders_for_archive(limit=10)

        assert state.archive_scan_status["done"] is True
        assert state.archive_scan_results == []

    def test_invalid_limit_reports_error(self):
        scan_senders_for_archive(limit=0)

        assert state.archive_scan_status["error"] == "Limit must be greater than 0"
        assert state.archive_scan_status["done"] is True
