"""
Tests for Gmail Restore Operations
------------------------------------
Restoring a logged entry is always the same generic operation: batchModify
with addLabelIds/removeLabelIds swapped from what the original action used.
"""

from unittest.mock import MagicMock, patch

from app.services import accounts, operation_log
from app.services.gmail.restore import restore_operation


def _mock_service():
    service = MagicMock()
    service.users.return_value.messages.return_value.batchModify.return_value.execute.return_value = {}
    return service


class TestRestoreOperation:
    @patch("app.services.gmail.restore.get_gmail_service")
    def test_restore_swaps_added_and_removed_labels(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)
        entry = operation_log.append_entry(
            action_type="delete",
            message_ids=["m1", "m2"],
            added_labels=["TRASH"],
            removed_labels=[],
            summary={"senders": ["a@example.com"]},
        )

        result = restore_operation(entry["id"])

        assert result["success"] is True
        assert result["restored"] == 2
        batch_modify = service.users.return_value.messages.return_value.batchModify
        body = batch_modify.call_args.kwargs["body"]
        assert body["ids"] == ["m1", "m2"]
        assert "addLabelIds" not in body  # nothing to re-add for a delete
        assert body["removeLabelIds"] == ["TRASH"]

    @patch("app.services.gmail.restore.get_gmail_service")
    def test_restore_archive_re_adds_inbox(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)
        entry = operation_log.append_entry(
            action_type="archive",
            message_ids=["m1"],
            added_labels=[],
            removed_labels=["INBOX"],
            summary={"senders": ["a@example.com"]},
        )

        restore_operation(entry["id"])

        body = service.users.return_value.messages.return_value.batchModify.call_args.kwargs[
            "body"
        ]
        assert body["addLabelIds"] == ["INBOX"]

    @patch("app.services.gmail.restore.get_gmail_service")
    def test_successful_restore_removes_entry_from_log(self, mock_get_service):
        service = _mock_service()
        mock_get_service.return_value = (service, None)
        entry = operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})

        restore_operation(entry["id"])

        assert operation_log.find_entry(entry["id"]) is None

    def test_restore_unknown_entry_returns_not_found(self):
        result = restore_operation("does-not-exist")

        assert result["success"] is False
        assert result["message"] == "Entry not found"

    @patch("app.services.gmail.restore.get_gmail_service")
    def test_failed_gmail_call_preserves_log_entry(self, mock_get_service):
        service = MagicMock()
        service.users.return_value.messages.return_value.batchModify.return_value.execute.side_effect = Exception(
            "Gmail API error"
        )
        mock_get_service.return_value = (service, None)
        entry = operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})

        result = restore_operation(entry["id"])

        assert result["success"] is False
        assert operation_log.find_entry(entry["id"]) is not None

    @patch("app.services.gmail.restore.get_gmail_service")
    def test_auth_error_returns_failure(self, mock_get_service):
        mock_get_service.return_value = (None, "Not authenticated")
        entry = operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})

        result = restore_operation(entry["id"])

        assert result["success"] is False
        assert result["message"] == "Not authenticated"


class TestRestoreAccountScoping:
    """Phase 4a: restore must never replay a batchModify against message IDs
    that belong to a different mailbox than the one currently active."""

    @patch("app.services.gmail.restore.get_gmail_service")
    def test_restore_blocked_for_entry_belonging_to_a_different_account(
        self, mock_get_service
    ):
        accounts.register_account("active@example.com")
        accounts.set_active_account("active@example.com")
        entry = operation_log.append_entry(
            "delete", ["m1"], ["TRASH"], [], {}, account_email="other@example.com"
        )

        result = restore_operation(entry["id"])

        assert result["success"] is False
        assert result["message"] == "Entry not found"
        mock_get_service.assert_not_called()

    @patch("app.services.gmail.restore.get_gmail_service")
    def test_restore_succeeds_for_entry_belonging_to_the_active_account(
        self, mock_get_service
    ):
        accounts.register_account("active@example.com")
        accounts.set_active_account("active@example.com")
        service = _mock_service()
        mock_get_service.return_value = (service, None)
        entry = operation_log.append_entry(
            "delete", ["m1"], ["TRASH"], [], {}, account_email="active@example.com"
        )

        result = restore_operation(entry["id"])

        assert result["success"] is True
