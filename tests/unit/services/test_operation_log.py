"""
Tests for the Operation Log
------------------------------
Pure JSON persistence for Phase 2 (Restore-from-Trash): append/list/remove
and 30-day retention pruning. No Gmail API calls here — see
tests/unit/services/gmail/test_restore.py for the Gmail-facing restore flow.
"""

import json
from datetime import datetime, timedelta, timezone

from app.services import operation_log


def _write_raw_entries(entries: list[dict]) -> None:
    path = operation_log._operation_log_path()
    import os

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(entries, f)


class TestAppendEntry:
    def test_append_returns_entry_with_generated_fields(self):
        entry = operation_log.append_entry(
            action_type="delete",
            message_ids=["m1", "m2"],
            added_labels=["TRASH"],
            removed_labels=[],
            summary={"senders": ["a@example.com"]},
        )

        assert entry["action_type"] == "delete"
        assert entry["message_ids"] == ["m1", "m2"]
        assert entry["source"] == "manual"
        assert entry["id"]
        assert entry["timestamp"]

    def test_append_persists_to_disk(self):
        operation_log.append_entry(
            action_type="archive",
            message_ids=["m1"],
            added_labels=[],
            removed_labels=["INBOX"],
            summary={},
        )

        entries = operation_log.list_entries()
        assert len(entries) == 1
        assert entries[0]["action_type"] == "archive"


class TestListEntries:
    def test_list_returns_reverse_chronological(self):
        older = operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})
        # Force distinct timestamps so ordering is unambiguous.
        entries = operation_log.list_entries()
        entries[0]["timestamp"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).isoformat()
        _write_raw_entries(entries)

        newer = operation_log.append_entry("archive", ["m2"], [], ["INBOX"], {})

        result = operation_log.list_entries()
        assert result[0]["id"] == newer["id"]
        assert result[1]["id"] == older["id"]

    def test_list_empty_when_no_file(self):
        assert operation_log.list_entries() == []

    def test_list_prunes_entries_older_than_30_days(self):
        stale = {
            "id": "old-entry",
            "timestamp": (
                datetime.now(timezone.utc) - timedelta(days=31)
            ).isoformat(),
            "action_type": "delete",
            "source": "manual",
            "message_ids": ["m1"],
            "added_labels": ["TRASH"],
            "removed_labels": [],
            "summary": {},
        }
        fresh = {
            **stale,
            "id": "fresh-entry",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        _write_raw_entries([stale, fresh])

        result = operation_log.list_entries()

        assert [e["id"] for e in result] == ["fresh-entry"]

    def test_list_persists_pruning_to_disk(self):
        stale = {
            "id": "old-entry",
            "timestamp": (
                datetime.now(timezone.utc) - timedelta(days=45)
            ).isoformat(),
            "action_type": "delete",
            "source": "manual",
            "message_ids": ["m1"],
            "added_labels": ["TRASH"],
            "removed_labels": [],
            "summary": {},
        }
        _write_raw_entries([stale])

        operation_log.list_entries()

        with open(operation_log._operation_log_path(), "r") as f:
            raw = json.load(f)
        assert raw == []


class TestFindEntry:
    def test_find_returns_matching_entry(self):
        entry = operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})
        assert operation_log.find_entry(entry["id"])["id"] == entry["id"]

    def test_find_returns_none_for_unknown_id(self):
        assert operation_log.find_entry("does-not-exist") is None


class TestRemoveEntry:
    def test_remove_deletes_and_returns_entry(self):
        entry = operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})

        removed = operation_log.remove_entry(entry["id"])

        assert removed["id"] == entry["id"]
        assert operation_log.list_entries() == []

    def test_remove_unknown_id_is_a_no_op(self):
        operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})

        removed = operation_log.remove_entry("does-not-exist")

        assert removed is None
        assert len(operation_log.list_entries()) == 1


class TestAccountScoping:
    """Phase 4a: entries can be tagged/filtered by which account they ran against."""

    def test_append_stores_account_email(self):
        entry = operation_log.append_entry(
            "delete", ["m1"], ["TRASH"], [], {}, account_email="a@example.com"
        )
        assert entry["account_email"] == "a@example.com"

    def test_append_without_account_email_defaults_to_none(self):
        entry = operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})
        assert entry["account_email"] is None

    def test_list_filters_by_account_email(self):
        operation_log.append_entry(
            "delete", ["m1"], ["TRASH"], [], {}, account_email="a@example.com"
        )
        operation_log.append_entry(
            "archive", ["m2"], [], ["INBOX"], {}, account_email="b@example.com"
        )

        result = operation_log.list_entries(account_email="a@example.com")

        assert len(result) == 1
        assert result[0]["account_email"] == "a@example.com"

    def test_list_without_account_filter_returns_everything(self):
        operation_log.append_entry(
            "delete", ["m1"], ["TRASH"], [], {}, account_email="a@example.com"
        )
        operation_log.append_entry(
            "archive", ["m2"], [], ["INBOX"], {}, account_email="b@example.com"
        )

        assert len(operation_log.list_entries()) == 2

    def test_list_excludes_untagged_legacy_entries_when_filtering(self):
        _write_raw_entries(
            [
                {
                    "id": "legacy-entry",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action_type": "delete",
                    "source": "manual",
                    "message_ids": ["m1"],
                    "added_labels": ["TRASH"],
                    "removed_labels": [],
                    "summary": {},
                    # no account_email key at all - pre-Phase-4a entry
                }
            ]
        )

        assert operation_log.list_entries(account_email="a@example.com") == []
        assert len(operation_log.list_entries()) == 1

    def test_find_entry_scoped_to_account_returns_none_for_other_account(self):
        entry = operation_log.append_entry(
            "delete", ["m1"], ["TRASH"], [], {}, account_email="a@example.com"
        )

        assert (
            operation_log.find_entry(entry["id"], account_email="b@example.com")
            is None
        )
        assert (
            operation_log.find_entry(entry["id"], account_email="a@example.com")
            is not None
        )


class TestBackfillAccountEmail:
    def test_backfill_tags_untagged_entries(self):
        operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})
        operation_log.append_entry("archive", ["m2"], [], ["INBOX"], {})

        operation_log.backfill_account_email("legacy@example.com")

        entries = operation_log.list_entries(account_email="legacy@example.com")
        assert len(entries) == 2

    def test_backfill_does_not_overwrite_already_tagged_entries(self):
        operation_log.append_entry(
            "delete", ["m1"], ["TRASH"], [], {}, account_email="a@example.com"
        )

        operation_log.backfill_account_email("legacy@example.com")

        entries = operation_log.list_entries()
        assert entries[0]["account_email"] == "a@example.com"

    def test_backfill_is_a_no_op_when_nothing_untagged(self):
        operation_log.append_entry(
            "delete", ["m1"], ["TRASH"], [], {}, account_email="a@example.com"
        )

        operation_log.backfill_account_email("legacy@example.com")

        assert len(operation_log.list_entries(account_email="a@example.com")) == 1


class TestCorruptOrMissingFile:
    def test_missing_file_treated_as_empty(self):
        assert operation_log.list_entries() == []

    def test_empty_file_treated_as_empty(self):
        path = operation_log._operation_log_path()
        import os

        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()

        assert operation_log.list_entries() == []

    def test_corrupt_json_treated_as_empty(self):
        path = operation_log._operation_log_path()
        import os

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("{not valid json")

        assert operation_log.list_entries() == []
