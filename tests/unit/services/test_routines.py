"""
Tests for Routines Storage (Phase 4b)
----------------------------------------
Pure JSON persistence: create/list/get/delete a saved Routine, account
scoping, and last_run_at stamping. No Gmail API calls here - see
tests/unit/services/gmail/test_routines.py for preview/run.
"""

import json

from app.services import routines


def _write_raw_routines(entries: list[dict]) -> None:
    path = routines._routines_path()
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(entries, f)


class TestCreateRoutine:
    def test_create_returns_routine_with_generated_fields(self):
        routine = routines.create_routine(
            name="Market newsletters",
            senders=["a@example.com"],
            older_than="7d",
            actions=["delete"],
            account_email="me@example.com",
        )

        assert routine["id"]
        assert routine["created_at"]
        assert routine["name"] == "Market newsletters"
        assert routine["senders"] == ["a@example.com"]
        assert routine["older_than"] == "7d"
        assert routine["actions"] == ["delete"]
        assert routine["account_email"] == "me@example.com"
        assert routine["schedule"] is None
        assert routine["last_run_at"] is None
        assert routine["label_id"] is None
        assert routine["label_name"] is None

    def test_create_persists_to_disk(self):
        routines.create_routine(
            name="Test",
            senders=["a@example.com"],
            older_than="30d",
            actions=["archive"],
            account_email="me@example.com",
        )

        assert len(routines.list_routines()) == 1

    def test_create_with_label_action_stores_label_fields(self):
        routine = routines.create_routine(
            name="Test",
            senders=["a@example.com"],
            older_than="30d",
            actions=["label"],
            account_email="me@example.com",
            label_id="Label_1",
            label_name="Security",
        )

        assert routine["label_id"] == "Label_1"
        assert routine["label_name"] == "Security"


class TestListRoutines:
    def test_list_returns_most_recently_created_first(self):
        older = routines.create_routine(
            "Older", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )
        older["created_at"] = "2020-01-01T00:00:00+00:00"
        _write_raw_routines([older])

        newer = routines.create_routine(
            "Newer", ["b@example.com"], "7d", ["archive"], "me@example.com"
        )

        result = routines.list_routines()
        assert result[0]["id"] == newer["id"]
        assert result[1]["id"] == older["id"]

    def test_list_empty_when_no_file(self):
        assert routines.list_routines() == []

    def test_list_filters_by_account_email(self):
        routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "acct-a@example.com"
        )
        routines.create_routine(
            "B", ["b@example.com"], "7d", ["archive"], "acct-b@example.com"
        )

        result = routines.list_routines(account_email="acct-a@example.com")

        assert len(result) == 1
        assert result[0]["name"] == "A"

    def test_list_without_account_filter_returns_everything(self):
        routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "acct-a@example.com"
        )
        routines.create_routine(
            "B", ["b@example.com"], "7d", ["archive"], "acct-b@example.com"
        )

        assert len(routines.list_routines()) == 2


class TestGetRoutine:
    def test_get_returns_matching_routine(self):
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )

        assert routines.get_routine(routine["id"])["id"] == routine["id"]

    def test_get_returns_none_for_unknown_id(self):
        assert routines.get_routine("does-not-exist") is None

    def test_get_scoped_to_account_returns_none_for_other_account(self):
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "acct-a@example.com"
        )

        assert routines.get_routine(routine["id"], account_email="acct-b@example.com") is None
        assert routines.get_routine(routine["id"], account_email="acct-a@example.com") is not None


class TestDeleteRoutine:
    def test_delete_removes_and_returns_routine(self):
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )

        removed = routines.delete_routine(routine["id"])

        assert removed["id"] == routine["id"]
        assert routines.list_routines() == []

    def test_delete_unknown_id_is_a_no_op(self):
        routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )

        removed = routines.delete_routine("does-not-exist")

        assert removed is None
        assert len(routines.list_routines()) == 1

    def test_delete_scoped_to_account_does_not_remove_other_accounts_routine(self):
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "acct-a@example.com"
        )

        removed = routines.delete_routine(routine["id"], account_email="acct-b@example.com")

        assert removed is None
        assert len(routines.list_routines()) == 1


class TestMarkRoutineRun:
    def test_mark_run_sets_last_run_at(self):
        routine = routines.create_routine(
            "A", ["a@example.com"], "7d", ["delete"], "me@example.com"
        )
        assert routine["last_run_at"] is None

        routines.mark_routine_run(routine["id"])

        updated = routines.get_routine(routine["id"])
        assert updated["last_run_at"] is not None

    def test_mark_run_unknown_id_is_a_no_op(self):
        routines.mark_routine_run("does-not-exist")
        assert routines.list_routines() == []


class TestCorruptOrMissingFile:
    def test_missing_file_treated_as_empty(self):
        assert routines.list_routines() == []

    def test_empty_file_treated_as_empty(self):
        path = routines._routines_path()
        import os

        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()

        assert routines.list_routines() == []

    def test_corrupt_json_treated_as_empty(self):
        path = routines._routines_path()
        import os

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not valid json")

        assert routines.list_routines() == []
