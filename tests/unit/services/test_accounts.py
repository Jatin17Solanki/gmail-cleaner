"""
Tests for the Accounts Module (Phase 4a: Multi-account switcher)
-------------------------------------------------------------------
Pure storage/index logic: the registered-account list, the active-account
pointer, and legacy single-token migration. No Gmail API calls here - see
tests/unit/services/auth for get_gmail_service()'s use of this module.
"""

import os

import pytest

from app.services import accounts


@pytest.fixture(autouse=True)
def isolate_accounts(monkeypatch, tmp_path):
    """Point settings.token_file somewhere that mock_gmail_auth's blanket
    os.path.exists override (tests/conftest.py) won't intercept.

    That autouse fixture forces any path containing "token.json" to report
    as non-existent, which is right for auth-flow tests but would make the
    legacy-migration tests below unable to see a real file on disk. Using a
    filename that doesn't match "token.json"/"credentials.json" sidesteps it
    while still exercising the real filesystem via tmp_path.
    """
    monkeypatch.setattr(accounts.settings, "token_file", str(tmp_path / "legacy_store.json"))


class TestListAndActiveAccount:
    def test_list_accounts_empty_by_default(self):
        assert accounts.list_accounts() == []

    def test_get_active_account_none_by_default(self):
        assert accounts.get_active_account() is None

    def test_register_account_adds_to_list(self):
        accounts.register_account("a@example.com")
        assert accounts.list_accounts() == ["a@example.com"]

    def test_register_account_is_idempotent(self):
        accounts.register_account("a@example.com")
        accounts.register_account("a@example.com")
        assert accounts.list_accounts() == ["a@example.com"]

    def test_register_multiple_accounts_preserves_order(self):
        accounts.register_account("a@example.com")
        accounts.register_account("b@example.com")
        assert accounts.list_accounts() == ["a@example.com", "b@example.com"]

    def test_set_active_account_succeeds_for_registered_account(self):
        accounts.register_account("a@example.com")
        assert accounts.set_active_account("a@example.com") is True
        assert accounts.get_active_account() == "a@example.com"

    def test_set_active_account_fails_for_unknown_account(self):
        assert accounts.set_active_account("nope@example.com") is False
        assert accounts.get_active_account() is None


class TestRemoveAccount:
    def test_remove_account_drops_it_from_list(self):
        accounts.register_account("a@example.com")
        accounts.remove_account("a@example.com")
        assert accounts.list_accounts() == []

    def test_remove_account_deletes_its_token_file(self):
        accounts.register_account("a@example.com")
        token_path = accounts.token_path_for("a@example.com")
        os.makedirs(os.path.dirname(token_path), exist_ok=True)
        with open(token_path, "w") as f:
            f.write("{}")

        accounts.remove_account("a@example.com")

        assert not os.path.exists(token_path)

    def test_removing_active_account_promotes_a_remaining_one(self):
        accounts.register_account("a@example.com")
        accounts.register_account("b@example.com")
        accounts.set_active_account("a@example.com")

        accounts.remove_account("a@example.com")

        assert accounts.get_active_account() == "b@example.com"

    def test_removing_only_active_account_clears_active_pointer(self):
        accounts.register_account("a@example.com")
        accounts.set_active_account("a@example.com")

        accounts.remove_account("a@example.com")

        assert accounts.get_active_account() is None

    def test_removing_non_active_account_leaves_active_pointer(self):
        accounts.register_account("a@example.com")
        accounts.register_account("b@example.com")
        accounts.set_active_account("a@example.com")

        accounts.remove_account("b@example.com")

        assert accounts.get_active_account() == "a@example.com"

    def test_remove_unknown_account_is_a_no_op(self):
        accounts.register_account("a@example.com")
        accounts.remove_account("nope@example.com")
        assert accounts.list_accounts() == ["a@example.com"]


class TestTokenPathFor:
    def test_sanitizes_path_separators(self):
        # Dots alone (e.g. from "..") aren't a traversal risk once path
        # separators are stripped - a single filename component with no
        # "/" or "\\" in it can't escape the tokens directory.
        path = accounts.token_path_for("weird/../name@example.com")
        basename = os.path.basename(path)
        assert "/" not in basename
        assert "\\" not in basename
        assert os.path.dirname(path) == accounts._tokens_dir()

    def test_normal_email_round_trips_readably(self):
        path = accounts.token_path_for("person@example.com")
        assert "person@example.com" in path


class TestResolveActiveTokenPath:
    def test_none_when_nothing_registered_and_no_legacy_file(self):
        assert accounts.resolve_active_token_path() is None

    def test_returns_active_accounts_token_path(self):
        accounts.register_account("a@example.com")
        accounts.set_active_account("a@example.com")

        assert accounts.resolve_active_token_path() == accounts.token_path_for(
            "a@example.com"
        )

    def test_falls_back_to_legacy_token_file_when_no_account_registered(self):
        os.makedirs(os.path.dirname(accounts.settings.token_file), exist_ok=True)
        with open(accounts.settings.token_file, "w") as f:
            f.write("{}")

        assert accounts.resolve_active_token_path() == accounts.settings.token_file


class TestMigrateLegacyToken:
    def test_migrates_legacy_file_into_per_account_storage(self):
        legacy_path = accounts.settings.token_file
        os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
        with open(legacy_path, "w") as f:
            f.write('{"token": "abc"}')

        accounts.migrate_legacy_token("a@example.com")

        new_path = accounts.token_path_for("a@example.com")
        assert os.path.exists(new_path)
        assert not os.path.exists(legacy_path)
        with open(new_path) as f:
            assert f.read() == '{"token": "abc"}'

    def test_migration_registers_and_activates_the_account(self):
        legacy_path = accounts.settings.token_file
        os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
        with open(legacy_path, "w") as f:
            f.write("{}")

        accounts.migrate_legacy_token("a@example.com")

        assert accounts.list_accounts() == ["a@example.com"]
        assert accounts.get_active_account() == "a@example.com"

    def test_no_op_when_no_legacy_file_exists(self):
        accounts.migrate_legacy_token("a@example.com")
        # Nothing to migrate, but the email is still registered so a first-
        # ever sign-in (no legacy file at all) still ends up active.
        assert accounts.list_accounts() == ["a@example.com"]
        assert accounts.get_active_account() == "a@example.com"

    def test_no_op_when_an_account_is_already_registered(self):
        accounts.register_account("existing@example.com")
        accounts.set_active_account("existing@example.com")
        legacy_path = accounts.settings.token_file
        os.makedirs(os.path.dirname(legacy_path), exist_ok=True)
        with open(legacy_path, "w") as f:
            f.write("{}")

        accounts.migrate_legacy_token("new@example.com")

        # Legacy file untouched, active account unchanged - this instance
        # was already multi-account, so there's nothing to migrate.
        assert os.path.exists(legacy_path)
        assert accounts.get_active_account() == "existing@example.com"
