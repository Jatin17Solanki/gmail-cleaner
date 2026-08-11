"""
Tests for Gmail Archive Operations
------------------------------------
No test file existed for archive.py before Phase 2 (pre-existing coverage
gap, not backfilled here per CLAUDE.md's scope-creep guidance). This file is
scoped narrowly to Phase 2's operation-log behavior: a successful archive
must be restorable.
"""

from unittest.mock import MagicMock, patch

from app.services import operation_log
from app.services.gmail.archive import archive_emails_background


def _mock_service(list_results=None):
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        list_results if list_results is not None else {"messages": []}
    )
    return service


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
