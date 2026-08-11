"""
Tests for Gmail Mark-as-Read Operations
------------------------------------------
No test file existed for mark_read.py before Phase 2 (pre-existing coverage
gap, not backfilled here per CLAUDE.md's scope-creep guidance). This file is
scoped narrowly to Phase 2's operation-log behavior: a successful mark-read
must be restorable.
"""

from unittest.mock import MagicMock, patch

from app.services import operation_log
from app.services.gmail.mark_read import mark_emails_as_read


def _mock_service(list_results=None):
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        list_results if list_results is not None else {"messages": []}
    )
    return service


class TestMarkReadWritesOperationLog:
    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_mark_read_logs_entry(self, mock_get_service):
        service = _mock_service({"messages": [{"id": "m1"}, {"id": "m2"}]})
        mock_get_service.return_value = (service, None)

        mark_emails_as_read(count=10)

        entries = operation_log.list_entries()
        assert len(entries) == 1
        entry = entries[0]
        assert entry["action_type"] == "mark_read"
        assert set(entry["message_ids"]) == {"m1", "m2"}
        assert entry["added_labels"] == []
        assert entry["removed_labels"] == ["UNREAD"]

    @patch("app.services.gmail.mark_read.get_gmail_service")
    def test_no_unread_messages_does_not_log(self, mock_get_service):
        service = _mock_service({"messages": []})
        mock_get_service.return_value = (service, None)

        mark_emails_as_read(count=10)

        assert operation_log.list_entries() == []
