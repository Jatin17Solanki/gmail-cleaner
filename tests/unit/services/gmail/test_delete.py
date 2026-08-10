"""
Tests for Gmail Delete Operations
----------------------------------
Tests for query scoping in delete.py: a delete call must only affect the
filtered subset of messages the user actually reviewed, not every message
from that sender (#107), and defaults to Inbox-only like the scan (#104).
"""

from unittest.mock import MagicMock, patch

from app.core import state
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
        assert any(
            "from:a@example.com" in q and "older_than:90d" in q for q in queries
        )
        assert any(
            "from:b@example.com" in q and "older_than:90d" in q for q in queries
        )


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
