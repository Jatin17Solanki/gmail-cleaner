"""
Tests for Gmail Label Management Operations
---------------------------------------------
Tests for query scoping in labels.py — mirrors the #107 fix for delete.py:
_apply_label_operation_background() previously re-queried Gmail via a bare
f-string, ignoring whatever filters were active in the scan.
"""

from unittest.mock import MagicMock, patch

from app.services import operation_log
from app.services.gmail.labels import (
    apply_label_to_senders_background,
    remove_label_from_senders_background,
)


def _mock_service(list_results=None, label_name="Newsletters"):
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        list_results if list_results is not None else {"messages": []}
    )
    service.users.return_value.labels.return_value.get.return_value.execute.return_value = {
        "name": label_name
    }
    return service


class TestApplyLabelQueryScoping:
    @patch("app.services.gmail.labels.get_gmail_service")
    def test_apply_label_scoped_to_sender_and_active_filters(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        apply_label_to_senders_background(
            "Label_1", ["a@example.com"], {"older_than": "30d"}
        )

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:a@example.com" in query
        assert "older_than:30d" in query

    @patch("app.services.gmail.labels.get_gmail_service")
    def test_apply_label_defaults_to_inbox_scope(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        apply_label_to_senders_background("Label_1", ["a@example.com"], None)

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "label:INBOX" in query


class TestRemoveLabelQueryScoping:
    @patch("app.services.gmail.labels.get_gmail_service")
    def test_remove_label_scoped_to_sender_and_label_name(self, mock_get_service):
        service = _mock_service(label_name="Newsletters")
        mock_get_service.return_value = (service, None)

        remove_label_from_senders_background(
            "Label_1", ["a@example.com"], {"older_than": "30d"}
        )

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:a@example.com" in query
        assert "older_than:30d" in query
        assert "label:Newsletters" in query

    @patch("app.services.gmail.labels.get_gmail_service")
    def test_remove_label_does_not_use_a_bare_sender_query(self, mock_get_service):
        """Regression test: previously this was `f"from:{sender} label:{name}"`,
        ignoring any active scan filters (e.g. the Inbox-only default, #104)."""
        service = _mock_service(label_name="Newsletters")
        mock_get_service.return_value = (service, None)

        remove_label_from_senders_background("Label_1", ["a@example.com"], None)

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:a@example.com" in query
        assert "label:Newsletters" in query
        assert "label:INBOX" in query


class TestLabelOperationsWriteOperationLog:
    """Phase 2: a successful label add/remove should be restorable."""

    @patch("app.services.gmail.labels.get_gmail_service")
    def test_apply_label_logs_entry(self, mock_get_service):
        service = _mock_service(
            {"messages": [{"id": "m1"}, {"id": "m2"}]}, label_name="Newsletters"
        )
        mock_get_service.return_value = (service, None)

        apply_label_to_senders_background("Label_1", ["a@example.com"])

        entries = operation_log.list_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action_type"] == "label_add"
        assert set(entry["message_ids"]) == {"m1", "m2"}
        assert entry["added_labels"] == ["Label_1"]
        assert entry["removed_labels"] == []
        assert entry["summary"]["senders"] == ["a@example.com"]
        assert entry["summary"]["label_name"] == "Newsletters"

    @patch("app.services.gmail.labels.get_gmail_service")
    def test_remove_label_logs_entry(self, mock_get_service):
        service = _mock_service(
            {"messages": [{"id": "m1"}]}, label_name="Newsletters"
        )
        mock_get_service.return_value = (service, None)

        remove_label_from_senders_background("Label_1", ["a@example.com"])

        entries = operation_log.list_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action_type"] == "label_remove"
        assert entry["message_ids"] == ["m1"]
        assert entry["added_labels"] == []
        assert entry["removed_labels"] == ["Label_1"]

    @patch("app.services.gmail.labels.get_gmail_service")
    def test_no_matching_messages_does_not_log(self, mock_get_service):
        service = _mock_service({"messages": []})
        mock_get_service.return_value = (service, None)

        apply_label_to_senders_background("Label_1", ["a@example.com"])

        assert operation_log.list_entries() == []


class TestLabelOperationsExclusion:
    """Phase 4c: per-message checkboxes exclude specific messages from an
    otherwise sender-wide label add/remove - query minus excluded, not an
    include-list (see delete.py's delete_emails_bulk_background for why)."""

    @patch("app.services.gmail.labels.get_gmail_service")
    def test_apply_label_skips_excluded_message(self, mock_get_service):
        service = _mock_service(
            {"messages": [{"id": "m1"}, {"id": "m2"}, {"id": "m3"}]}
        )
        mock_get_service.return_value = (service, None)

        apply_label_to_senders_background(
            "Label_1", ["a@example.com"], excluded_message_ids=["m2"]
        )

        batch_modify = service.users.return_value.messages.return_value.batchModify
        body = batch_modify.call_args.kwargs["body"]
        assert set(body["ids"]) == {"m1", "m3"}

    @patch("app.services.gmail.labels.get_gmail_service")
    def test_excluding_every_matched_message_labels_nothing(self, mock_get_service):
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        apply_label_to_senders_background(
            "Label_1", ["a@example.com"], excluded_message_ids=["m1"]
        )

        service.users.return_value.messages.return_value.batchModify.assert_not_called()
        assert operation_log.list_entries() == []
