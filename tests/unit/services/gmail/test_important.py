"""
Tests for Gmail Mark Important Operations
-------------------------------------------
No test file existed for important.py before Phase 3. Phase 3 makes
Important a per-row inline action across Delete/Mark-as-read/Archive (PRD
Section 5), which means it now needs to stay scoped to whichever view's
filters surfaced the sender - previously mark_important_background used a
bare f"from:{sender}" query with no filters param at all (the same #107
gap already fixed elsewhere). Scoped to that query-routing behavior.
"""

from unittest.mock import MagicMock, patch

from app.services.gmail.important import mark_important_background


def _mock_service(list_results=None):
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        list_results if list_results is not None else {"messages": []}
    )
    return service


class TestMarkImportantQueryScoping:
    """#107 pattern: important must only affect the filtered subset of
    messages the user actually reviewed, not every message from that
    sender regardless of location (Inbox, Archive, Trash, ...)."""

    @patch("app.services.gmail.important.get_gmail_service")
    def test_scoped_to_sender_and_inbox_by_default(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        mark_important_background(["newsletter@example.com"])

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "label:INBOX" in query

    @patch("app.services.gmail.important.get_gmail_service")
    def test_scoped_to_sender_and_active_filters(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        mark_important_background(
            ["newsletter@example.com"], filters={"category": "promotions"}
        )

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "category:promotions" in query
        assert "label:INBOX" not in query

    @patch("app.services.gmail.important.get_gmail_service")
    def test_does_not_use_a_bare_sender_query(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        mark_important_background(["newsletter@example.com"])

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert query != "from:newsletter@example.com"

    @patch("app.services.gmail.important.get_gmail_service")
    def test_unmark_uses_same_scoping(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        mark_important_background(
            ["newsletter@example.com"], important=False, filters={"older_than": "30d"}
        )

        list_call = service.users.return_value.messages.return_value.list
        query = list_call.call_args.kwargs["q"]
        assert "older_than:30d" in query

        batch_modify = service.users.return_value.messages.return_value.batchModify
        # No messages found in this mock, so batchModify is never called -
        # assert on the marking behavior instead where messages do exist.
        assert not batch_modify.called

    @patch("app.services.gmail.important.get_gmail_service")
    def test_mark_important_sets_important_label(self, mock_get_service):
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        mark_important_background(["newsletter@example.com"], important=True)

        batch_modify = service.users.return_value.messages.return_value.batchModify
        body = batch_modify.call_args.kwargs["body"]
        assert body["addLabelIds"] == ["IMPORTANT"]

    @patch("app.services.gmail.important.get_gmail_service")
    def test_unmark_important_removes_important_label(self, mock_get_service):
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        mark_important_background(["newsletter@example.com"], important=False)

        batch_modify = service.users.return_value.messages.return_value.batchModify
        body = batch_modify.call_args.kwargs["body"]
        assert body["removeLabelIds"] == ["IMPORTANT"]
