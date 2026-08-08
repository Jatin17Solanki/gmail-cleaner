"""
Tests for the App Login Gate
------------------------------
Tests for password verification and session management (Phase 1.3,
resolves #109/#108/#111).
"""

import json
import time

import pytest

from app.core import security


@pytest.fixture(autouse=True)
def _isolate_auth_state():
    """Each test gets a clean session store and no configured password."""
    security._active_sessions.clear()
    security.settings.app_password = None
    yield
    security._active_sessions.clear()
    security.settings.app_password = None


class TestAuthEnabled:
    def test_disabled_when_no_password_configured(self):
        assert security.is_auth_enabled() is False

    def test_enabled_when_password_configured(self):
        security.settings.app_password = "hunter2"
        assert security.is_auth_enabled() is True


class TestVerifyPassword:
    def test_correct_password_verifies(self):
        security.settings.app_password = "hunter2"
        assert security.verify_password("hunter2") is True

    def test_incorrect_password_fails(self):
        security.settings.app_password = "hunter2"
        assert security.verify_password("wrong") is False

    def test_fails_when_no_password_configured(self):
        assert security.verify_password("anything") is False

    def test_empty_submitted_password_fails(self):
        security.settings.app_password = "hunter2"
        assert security.verify_password("") is False


class TestSessions:
    def test_created_session_is_valid(self):
        token = security.create_session()
        assert security.is_valid_session(token) is True

    def test_unknown_token_is_invalid(self):
        assert security.is_valid_session("not-a-real-token") is False

    def test_missing_token_is_invalid(self):
        assert security.is_valid_session(None) is False

    def test_destroyed_session_is_invalid(self):
        token = security.create_session()
        security.destroy_session(token)
        assert security.is_valid_session(token) is False

    def test_destroying_unknown_token_is_a_noop(self):
        security.destroy_session("not-a-real-token")

    def test_expired_session_is_invalid(self):
        token = security.create_session()
        security._active_sessions[token] = (
            time.time() - security.SESSION_TTL_SECONDS - 1
        )
        assert security.is_valid_session(token) is False


class TestEnsurePasswordHashPersisted:
    def test_noop_when_no_password_configured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            security.settings, "token_file", str(tmp_path / "token.json")
        )
        security.ensure_password_hash_persisted()
        assert not (tmp_path / "auth.json").exists()

    def test_writes_hash_file_when_password_configured(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            security.settings, "token_file", str(tmp_path / "token.json")
        )
        security.settings.app_password = "hunter2"

        security.ensure_password_hash_persisted()

        auth_file = tmp_path / "auth.json"
        assert auth_file.exists()
        data = json.loads(auth_file.read_text())
        assert data["password_hash"] == security._hash_password("hunter2")
