"""
Tests for Gmail Label Management Operations
---------------------------------------------
Tests for query scoping in labels.py — mirrors the #107 fix for delete.py:
_apply_label_operation_background() previously re-queried Gmail via a bare
f-string, ignoring whatever filters were active in the scan.
"""

from unittest.mock import MagicMock, patch

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
