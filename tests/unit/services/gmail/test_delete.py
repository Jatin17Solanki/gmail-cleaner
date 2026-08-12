"""
Tests for Gmail Delete Operations
----------------------------------
Tests for query scoping in delete.py: a delete call must only affect the
filtered subset of messages the user actually reviewed, not every message
from that sender (#107), and defaults to Inbox-only like the scan (#104).
"""

from unittest.mock import MagicMock, patch

from app.core import state
from app.services import operation_log
from app.services.gmail.delete import (
    delete_emails_bulk_background,
    delete_emails_by_sender,
    scan_senders_for_delete,
)


def _mock_service(list_results=None):
    """Build a MagicMock Gmail service whose messages().list() returns list_results."""
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        list_results if list_results is not None else {"messages": []}
    )
    return service


def _mock_batch_service(message_ids, responses_by_id):
    """Build a MagicMock Gmail service whose messages().list() returns
    message_ids, and whose batch API feeds responses_by_id[msg_id] into
    scan_senders_for_delete's process_message callback."""
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


def _message_response(sender_email, subject, headers=None):
    all_headers = [
        {"name": "From", "value": f"Sender <{sender_email}>"},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": "Wed, 15 Nov 2025 10:30:00 +0000"},
    ] + (headers or [])
    return {
        "id": f"msg-{subject}",
        "sizeEstimate": 100,
        "payload": {"headers": all_headers},
    }


class TestDeleteEmailsBySenderQueryScoping:
    """Ensures delete queries route through build_gmail_query with active filters (#107)."""

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_delete_scoped_to_sender_and_inbox_by_default(self, mock_get_service):
        """With no filters, delete is scoped to sender + the Inbox default (#104)."""
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        delete_emails_by_sender("newsletter@example.com")

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "label:INBOX" in query

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_delete_scoped_to_sender_and_active_filters(self, mock_get_service):
        """Delete should only affect messages matching sender AND the scan's filters."""
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        delete_emails_by_sender(
            "newsletter@example.com", {"older_than": "30d", "category": "promotions"}
        )

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "older_than:30d" in query
        assert "category:promotions" in query
        # An explicit category shouldn't also force Inbox scoping
        assert "label:INBOX" not in query

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_delete_does_not_use_a_bare_sender_query(self, mock_get_service):
        """Regression test for #107: a bare 'from:sender' query ignores the
        filters the sender was actually found under, deleting more than the
        user selected."""
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        delete_emails_by_sender("newsletter@example.com", {"older_than": "7d"})

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert query != "from:newsletter@example.com"
        assert "older_than:7d" in query


class TestDeleteEmailsBulkBackgroundQueryScoping:
    """Same #107 fix, applied to the bulk/background delete path."""

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_bulk_delete_scopes_each_sender_to_active_filters(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        delete_emails_bulk_background(
            ["a@example.com", "b@example.com"], {"older_than": "90d"}
        )

        list_call = service.users.return_value.messages.return_value.list
        queries = [call.kwargs["q"] for call in list_call.call_args_list]
        assert any("from:a@example.com" in q and "older_than:90d" in q for q in queries)
        assert any("from:b@example.com" in q and "older_than:90d" in q for q in queries)


class TestDeleteEmailsBulkBackgroundExclusion:
    """Phase 4c: per-message checkboxes in an expanded sender row exclude
    specific messages from an otherwise sender-wide delete - query minus
    excluded, not an include-list (see delete_emails_bulk_background's
    docstring for why: the include-list interpretation would silently skip
    any mail beyond whatever happened to be previewed)."""

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_excluded_message_id_is_not_deleted(self, mock_get_service):
        service = _mock_service(
            {"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}
        )
        mock_get_service.return_value = (service, None)

        delete_emails_bulk_background(
            ["newsletter@example.com"], excluded_message_ids=["m2"]
        )

        batch_modify = service.users.return_value.messages.return_value.batchModify
        body = batch_modify.call_args.kwargs["body"]
        assert set(body["ids"]) == {"m1", "m3"}

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_excluding_every_matched_message_leaves_nothing_deleted(
        self, mock_get_service
    ):
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        delete_emails_bulk_background(
            ["newsletter@example.com"], excluded_message_ids=["m1"]
        )

        service.users.return_value.messages.return_value.batchModify.assert_not_called()
        assert operation_log.list_entries() == []

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_excluded_id_belonging_to_another_sender_is_a_no_op(
        self, mock_get_service
    ):
        """An excluded ID that never appears in this call's own query results
        (e.g. it came from a different sender's expanded row) must not
        affect anything - only IDs actually collected for this delete are
        eligible to be filtered out."""
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        delete_emails_bulk_background(
            ["newsletter@example.com"], excluded_message_ids=["unrelated-id"]
        )

        batch_modify = service.users.return_value.messages.return_value.batchModify
        assert batch_modify.call_args.kwargs["body"]["ids"] == ["m1"]


class TestScanSendersForDeletePersistsFilters:
    """The scan persists its filters so a later delete/label call can reuse them."""

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_scan_stores_active_filters_in_state(self, mock_get_service):
        mock_get_service.return_value = (None, "Not authenticated")

        scan_senders_for_delete(limit=10, filters={"older_than": "30d"})

        assert state.delete_scan_filters == {"older_than": "30d"}

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_scan_stores_none_when_no_filters_given(self, mock_get_service):
        mock_get_service.return_value = (None, "Not authenticated")

        scan_senders_for_delete(limit=10, filters=None)

        assert state.delete_scan_filters is None


class TestScanSendersForDeleteUnsubscribeDetection:
    """Phase 3: Unsubscribe is merged into the Delete scan (no separate tab),
    so scan_senders_for_delete must also surface per-sender unsubscribe
    link/type, same detection get_unsubscribe_from_headers already provides
    for the (now-retired) standalone unsubscribe scan."""

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_captures_one_click_unsubscribe_link(self, mock_get_service):
        response = _message_response(
            "newsletter@example.com",
            "Hello",
            headers=[
                {
                    "name": "List-Unsubscribe",
                    "value": "<https://example.com/unsub>",
                },
                {
                    "name": "List-Unsubscribe-Post",
                    "value": "List-Unsubscribe=One-Click",
                },
            ],
        )
        service = _mock_batch_service(["m1"], {"m1": response})
        mock_get_service.return_value = (service, None)

        scan_senders_for_delete(limit=10)

        sender = state.delete_scan_results[0]
        assert sender["unsubscribe_link"] == "https://example.com/unsub"
        assert sender["unsubscribe_type"] == "one-click"

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_captures_manual_unsubscribe_link(self, mock_get_service):
        response = _message_response(
            "newsletter@example.com",
            "Hello",
            headers=[
                {"name": "List-Unsubscribe", "value": "<https://example.com/unsub>"},
            ],
        )
        service = _mock_batch_service(["m1"], {"m1": response})
        mock_get_service.return_value = (service, None)

        scan_senders_for_delete(limit=10)

        sender = state.delete_scan_results[0]
        assert sender["unsubscribe_link"] == "https://example.com/unsub"
        assert sender["unsubscribe_type"] == "manual"

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_no_unsubscribe_header_leaves_link_none(self, mock_get_service):
        response = _message_response("no-reply@example.com", "OTP")
        service = _mock_batch_service(["m1"], {"m1": response})
        mock_get_service.return_value = (service, None)

        scan_senders_for_delete(limit=10)

        sender = state.delete_scan_results[0]
        assert sender["unsubscribe_link"] is None
        assert sender["unsubscribe_type"] is None


class TestScanSendersForDeleteSubjectsUncapped:
    """Phase 4c: subjects are stored 1:1 with message_ids, uncapped - the
    expanded-row's "Load more" pagination is a client-side reveal over
    already-fetched data, so the scan must not silently discard anything
    past the old 20-per-sender preview cap. Costs no extra API calls since
    headers are already fetched in the same batch metadata request."""

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_subjects_not_capped_at_twenty(self, mock_get_service):
        message_ids = [f"m{i}" for i in range(25)]
        responses = {
            mid: _message_response("newsletter@example.com", f"Subject {i}")
            for i, mid in enumerate(message_ids)
        }
        service = _mock_batch_service(message_ids, responses)
        mock_get_service.return_value = (service, None)

        scan_senders_for_delete(limit=25)

        sender = state.delete_scan_results[0]
        assert sender["count"] == 25
        assert len(sender["subjects"]) == 25
        assert len(sender["message_ids"]) == 25

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_subjects_align_with_message_ids_by_index(self, mock_get_service):
        """The frontend pairs subjects[i] with message_ids[i] to wire each
        preview row's checkbox/eye-icon to a real message ID - both lists
        must grow in lockstep, not just end up the same length."""
        message_ids = ["req-0", "req-1", "req-2"]
        responses = {
            mid: _message_response("newsletter@example.com", f"Subject-{mid}")
            for mid in message_ids
        }
        service = _mock_batch_service(message_ids, responses)
        mock_get_service.return_value = (service, None)

        scan_senders_for_delete(limit=3)

        sender = state.delete_scan_results[0]
        # _message_response encodes the subject into the response's own
        # "id" field (id = f"msg-{subject}") - since process_message reads
        # both msg_id and subject from that same response object, this
        # holding true for every pair is exactly what "lockstep" means.
        for msg_id, subject in zip(sender["message_ids"], sender["subjects"]):
            assert msg_id == f"msg-{subject}"


class TestScanSendersForDeleteTrueTotals:
    """Phase 4c follow-up: a sender's `count` only reflects the scanned
    window - the scan also fetches the real total (Gmail's own
    resultSizeEstimate) so the UI isn't showing a number that understates
    what a delete would actually affect."""

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_scan_result_includes_total_count(self, mock_get_service):
        response = _message_response("newsletter@example.com", "Hello")
        service = _mock_batch_service(["m1"], {"m1": response})
        mock_get_service.return_value = (service, None)
        # First call: the scan's own message-listing pagination (one
        # message). Second call: the true-count pass for that one sender.
        service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "m1"}]},
            {"resultSizeEstimate": 312},
        ]

        scan_senders_for_delete(limit=10)

        sender = state.delete_scan_results[0]
        assert sender["count"] == 1
        assert sender["total_count"] == 312


class TestDeleteEmailsWritesOperationLog:
    """Phase 2: a successful delete should be restorable via the operation log."""

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_delete_by_sender_logs_entry(self, mock_get_service):
        service = _mock_service({"messages": [{"id": "m1"}, {"id": "m2"}]})
        mock_get_service.return_value = (service, None)

        delete_emails_by_sender("newsletter@example.com")

        entries = operation_log.list_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action_type"] == "delete"
        assert set(entry["message_ids"]) == {"m1", "m2"}
        assert entry["added_labels"] == ["TRASH"]
        # Restoring must put deleted mail back in the Inbox, not just out of
        # Trash - Gmail doesn't restore INBOX membership on its own when the
        # TRASH label is removed, so the log has to record its removal too.
        assert entry["removed_labels"] == ["INBOX"]
        assert entry["summary"]["senders"] == ["newsletter@example.com"]

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_delete_by_sender_removes_inbox_label(self, mock_get_service):
        """Regression test: trashing via batchModify doesn't restore INBOX
        membership on its own when later untrashed - deleting must
        explicitly remove INBOX so a later restore can explicitly re-add it,
        putting the message back in the Inbox rather than stranding it in
        "All Mail" only."""
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        delete_emails_by_sender("newsletter@example.com")

        batch_modify = service.users.return_value.messages.return_value.batchModify
        body = batch_modify.call_args.kwargs["body"]
        assert body["addLabelIds"] == ["TRASH"]
        assert body["removeLabelIds"] == ["INBOX"]

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_delete_by_sender_no_op_does_not_log(self, mock_get_service):
        service = _mock_service({"messages": []})
        mock_get_service.return_value = (service, None)

        delete_emails_by_sender("newsletter@example.com")

        assert operation_log.list_entries() == []

    @patch("app.services.gmail.delete.get_gmail_service")
    def test_bulk_delete_logs_one_entry_for_all_senders(self, mock_get_service):
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        delete_emails_bulk_background(["a@example.com", "b@example.com"])

        entries = operation_log.list_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action_type"] == "delete"
        assert entry["removed_labels"] == ["INBOX"]
        assert entry["summary"]["senders"] == ["a@example.com", "b@example.com"]
        # One matching message per sender, from the shared mock list() response
        assert len(entry["message_ids"]) == 2
