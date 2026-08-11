"""
Tests for Multi-Account Auth Behavior (Phase 4a)
----------------------------------------------------
switch_active_account(), sign_out()'s per-account scoping, get_gmail_service's
add_new_account flag, and legacy-token migration-on-success. Complements the
existing OAuth-flow/token-management test files, which cover the
single-account-shaped mechanics (background thread, timeouts, credential
parsing) that this phase didn't change.
"""

import os
from unittest.mock import Mock, patch

from app.core import state
from app.services import accounts, auth, operation_log


def _write_token(path: str, contents: str = '{"token": "abc"}') -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(contents)


class TestSwitchActiveAccount:
    def test_switch_to_known_account_succeeds(self):
        accounts.register_account("a@example.com")
        accounts.register_account("b@example.com")
        accounts.set_active_account("a@example.com")

        result = auth.switch_active_account("b@example.com")

        assert result["success"] is True
        assert accounts.get_active_account() == "b@example.com"

    def test_switch_to_unknown_account_fails_without_changing_active(self):
        accounts.register_account("a@example.com")
        accounts.set_active_account("a@example.com")

        result = auth.switch_active_account("nope@example.com")

        assert result["success"] is False
        assert accounts.get_active_account() == "a@example.com"

    def test_switch_clears_per_account_scan_state(self):
        accounts.register_account("a@example.com")
        accounts.register_account("b@example.com")
        accounts.set_active_account("a@example.com")
        state.delete_scan_results = [{"email": "leftover@example.com"}]
        state.current_user = {"email": "a@example.com", "logged_in": True}

        auth.switch_active_account("b@example.com")

        assert state.delete_scan_results == []
        assert state.current_user == {"email": None, "logged_in": False}


class TestSignOutMultiAccount:
    def test_sign_out_removes_active_accounts_token(self):
        accounts.register_account("a@example.com")
        accounts.set_active_account("a@example.com")
        token_path = accounts.token_path_for("a@example.com")
        _write_token(token_path)

        auth.sign_out()

        assert not os.path.exists(token_path)
        assert "a@example.com" not in accounts.list_accounts()

    def test_sign_out_promotes_a_remaining_account(self):
        accounts.register_account("a@example.com")
        accounts.register_account("b@example.com")
        accounts.set_active_account("a@example.com")
        _write_token(accounts.token_path_for("a@example.com"))
        _write_token(accounts.token_path_for("b@example.com"))

        result = auth.sign_out()

        assert accounts.get_active_account() == "b@example.com"
        assert result["remaining_accounts"] == ["b@example.com"]

    def test_sign_out_with_no_other_accounts_clears_active_pointer(self):
        accounts.register_account("a@example.com")
        accounts.set_active_account("a@example.com")
        _write_token(accounts.token_path_for("a@example.com"))

        auth.sign_out()

        assert accounts.get_active_account() is None
        assert accounts.list_accounts() == []


class TestAddNewAccountSkipsExistingCreds:
    @patch("app.services.auth._auth_in_progress", {"active": False, "started_at": None})
    @patch("app.services.auth.Credentials")
    def test_add_new_account_does_not_load_active_accounts_token(
        self, mock_creds_class
    ):
        """The whole point of add_new_account=True: an already-signed-in
        account's valid token must never be reused for the "Add another
        account" flow - it always needs a fresh consent screen."""
        accounts.register_account("active@example.com")
        accounts.set_active_account("active@example.com")
        _write_token(accounts.token_path_for("active@example.com"))
        valid_creds = Mock(valid=True)
        mock_creds_class.from_authorized_user_file.return_value = valid_creds

        with patch("app.services.auth._get_credentials_path", return_value=None):
            service, error = auth.get_gmail_service(add_new_account=True)

        assert service is None
        assert "credentials.json" in error
        mock_creds_class.from_authorized_user_file.assert_not_called()

    @patch("app.services.auth.Credentials")
    def test_without_add_new_account_the_active_token_is_used(
        self, mock_creds_class
    ):
        accounts.register_account("active@example.com")
        accounts.set_active_account("active@example.com")
        token_path = accounts.token_path_for("active@example.com")
        _write_token(token_path)
        valid_creds = Mock(valid=True)
        mock_creds_class.from_authorized_user_file.return_value = valid_creds

        with patch("app.services.auth.build") as mock_build:
            mock_build.return_value.users.return_value.getProfile.return_value.execute.return_value = {
                "emailAddress": "active@example.com"
            }
            service, error = auth.get_gmail_service()

        assert error is None
        mock_creds_class.from_authorized_user_file.assert_called_once_with(
            token_path, auth.settings.scopes
        )


class TestLegacyTokenMigrationOnSuccess:
    @patch("app.services.auth.Credentials")
    @patch("app.services.auth.build")
    def test_successful_profile_fetch_migrates_legacy_token(
        self, mock_build, mock_creds_class, monkeypatch, tmp_path
    ):
        # Use a filename that doesn't contain "token.json" so
        # tests/conftest.py's blanket os.path.exists mock (mock_gmail_auth)
        # doesn't force this legacy file to look nonexistent.
        legacy_path = str(tmp_path / "legacy_creds.json")
        monkeypatch.setattr(auth.settings, "token_file", legacy_path)
        _write_token(legacy_path)

        valid_creds = Mock(valid=True)
        mock_creds_class.from_authorized_user_file.return_value = valid_creds
        mock_build.return_value.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "migrated@example.com"
        }
        operation_log.append_entry("delete", ["m1"], ["TRASH"], [], {})

        service, error = auth.get_gmail_service()

        assert error is None
        assert accounts.get_active_account() == "migrated@example.com"
        assert not os.path.exists(legacy_path)
        assert os.path.exists(accounts.token_path_for("migrated@example.com"))
        backfilled = operation_log.list_entries(account_email="migrated@example.com")
        assert len(backfilled) == 1
