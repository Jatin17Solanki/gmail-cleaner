"""
Tests for Gmail Routine Execution (Phase 4b)
-----------------------------------------------
Preview (counts only, no writes) and run (combined action diff, one
operation-log entry per run) for a saved Routine. See
tests/unit/services/test_routines.py for the pure CRUD storage layer.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.core import state
from app.services import accounts, operation_log, routines
from app.services.gmail.routines import (
    _label_diff_for_actions,
    get_routine_run_status,
    preview_routine,
    run_routine_background,
)


def _mock_service(list_results=None):
    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        list_results if list_results is not None else {"messages": []}
    )
    service.users.return_value.messages.return_value.batchModify.return_value.execute.return_value = {}
    return service


def _activate_account(email: str = "me@example.com") -> None:
    accounts.register_account(email)
    accounts.set_active_account(email)


class TestLabelDiffForActions:
    def test_delete_adds_trash_removes_inbox(self):
        add, remove = _label_diff_for_actions(["delete"], None)
        assert add == ["TRASH"]
        assert remove == ["INBOX"]

    def test_archive_only_removes_inbox(self):
        add, remove = _label_diff_for_actions(["archive"], None)
        assert add == []
        assert remove == ["INBOX"]

    def test_mark_read_removes_unread(self):
        add, remove = _label_diff_for_actions(["mark_read"], None)
        assert add == []
        assert remove == ["UNREAD"]

    def test_label_adds_the_given_label_id(self):
        add, remove = _label_diff_for_actions(["label"], "Label_1")
        assert add == ["Label_1"]
        assert remove == []

    def test_label_without_label_id_adds_nothing(self):
        add, remove = _label_diff_for_actions(["label"], None)
        assert add == []

    def test_combined_delete_and_label(self):
        add, remove = _label_diff_for_actions(["delete", "label"], "Label_1")
        assert set(add) == {"TRASH", "Label_1"}
        assert remove == ["INBOX"]

    def test_combined_archive_and_mark_read(self):
        add, remove = _label_diff_for_actions(["archive", "mark_read"], None)
        assert add == []
        assert set(remove) == {"INBOX", "UNREAD"}


class TestPreviewRoutine:
    def test_returns_none_for_unknown_routine(self):
        assert preview_routine("does-not-exist") is None

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_raises_on_auth_error(self, mock_get_service):
        _activate_account()
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )
        mock_get_service.return_value = (None, "Not authenticated")

        with pytest.raises(RuntimeError, match="Not authenticated"):
            preview_routine(routine["id"])

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_sums_counts_across_senders(self, mock_get_service):
        _activate_account()
        routine = routines.create_routine(
            "A",
            ["a@example.com", "b@example.com"],
            "7d",
            ["delete"],
            "me@example.com",
        )
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "m1"}, {"id": "m2"}]},  # a@example.com
            {"messages": [{"id": "m3"}]},  # b@example.com
        ]
        mock_get_service.return_value = (service, None)

        preview = preview_routine(routine["id"])

        assert preview["total"] == 3
        assert preview["per_sender"] == [
            {"sender": "a@example.com", "count": 2},
            {"sender": "b@example.com", "count": 1},
        ]
        assert preview["actions"] == ["delete"]
        assert preview["name"] == "A"

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_paginates_per_sender(self, mock_get_service):
        _activate_account()
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )
        service = MagicMock()
        service.users.return_value.messages.return_value.list.return_value.execute.side_effect = [
            {"messages": [{"id": "m1"}], "nextPageToken": "p2"},
            {"messages": [{"id": "m2"}]},
        ]
        mock_get_service.return_value = (service, None)

        preview = preview_routine(routine["id"])

        assert preview["total"] == 2

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_query_scoped_to_sender_and_older_than(self, mock_get_service):
        _activate_account()
        routine = routines.create_routine(
            "A", ["newsletter@example.com"], "30d", ["delete"], "me@example.com"
        )
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        preview_routine(routine["id"])

        query = service.users.return_value.messages.return_value.list.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "older_than:30d" in query

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_scoped_to_active_account(self, mock_get_service):
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "acct-a@example.com"
        )
        accounts.register_account("acct-b@example.com")
        accounts.set_active_account("acct-b@example.com")

        assert preview_routine(routine["id"]) is None
        mock_get_service.assert_not_called()


class TestRunRoutineBackground:
    def test_unknown_routine_reports_error(self):
        run_routine_background("does-not-exist")

        assert state.routine_run_status["done"] is True
        assert state.routine_run_status["error"] == "Routine not found"

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_auth_error_reports_error(self, mock_get_service):
        _activate_account()
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )
        mock_get_service.return_value = (None, "Not authenticated")

        run_routine_background(routine["id"])

        assert state.routine_run_status["done"] is True
        assert state.routine_run_status["error"] == "Not authenticated"

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_delete_action_applies_combined_diff_and_logs(self, mock_get_service):
        _activate_account("me@example.com")
        routine = routines.create_routine(
            "Market newsletters",
            ["a@example.com"],
            "7d",
            ["delete"],
            "me@example.com",
        )
        service = _mock_service({"messages": [{"id": "m1"}, {"id": "m2"}]})
        mock_get_service.return_value = (service, None)

        run_routine_background(routine["id"])

        assert state.routine_run_status["done"] is True
        assert state.routine_run_status["affected_count"] == 2

        body = service.users.return_value.messages.return_value.batchModify.call_args.kwargs[
            "body"
        ]
        assert set(body["ids"]) == {"m1", "m2"}
        assert body["addLabelIds"] == ["TRASH"]
        assert body["removeLabelIds"] == ["INBOX"]

        entries = operation_log.list_entries()
        assert len(entries) == 1
        assert entries[0]["action_type"] == "routine"
        assert entries[0]["source"] == "Market newsletters"
        assert entries[0]["account_email"] == "me@example.com"
        assert set(entries[0]["message_ids"]) == {"m1", "m2"}

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_combined_label_and_archive_actions(self, mock_get_service):
        _activate_account()
        routine = routines.create_routine(
            "A",
            ["a@example.com"],
            "7d",
            ["archive", "label"],
            "me@example.com",
            label_id="Label_1",
        )
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        run_routine_background(routine["id"])

        body = service.users.return_value.messages.return_value.batchModify.call_args.kwargs[
            "body"
        ]
        assert body["addLabelIds"] == ["Label_1"]
        assert body["removeLabelIds"] == ["INBOX"]

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_no_matching_messages_does_not_log_but_still_marks_run(
        self, mock_get_service
    ):
        _activate_account()
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )
        service = _mock_service({"messages": []})
        mock_get_service.return_value = (service, None)

        run_routine_background(routine["id"])

        assert operation_log.list_entries() == []
        assert state.routine_run_status["message"] == "No matching emails found"
        assert routines.get_routine(routine["id"])["last_run_at"] is not None

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_stamps_last_run_at_on_success(self, mock_get_service):
        _activate_account()
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )
        assert routine["last_run_at"] is None
        service = _mock_service({"messages": [{"id": "m1"}]})
        mock_get_service.return_value = (service, None)

        run_routine_background(routine["id"])

        assert routines.get_routine(routine["id"])["last_run_at"] is not None

    @patch("app.services.gmail.routines.get_gmail_service")
    def test_query_scoped_to_sender_and_older_than(self, mock_get_service):
        _activate_account()
        routine = routines.create_routine(
            "A", ["newsletter@example.com"], "90d", ["archive"], "me@example.com"
        )
        service = _mock_service()
        mock_get_service.return_value = (service, None)

        run_routine_background(routine["id"])

        query = service.users.return_value.messages.return_value.list.call_args.kwargs["q"]
        assert "from:newsletter@example.com" in query
        assert "older_than:90d" in query

    def test_get_routine_run_status_returns_a_copy(self):
        status = get_routine_run_status()
        status["message"] = "mutated"

        assert state.routine_run_status["message"] != "mutated"
