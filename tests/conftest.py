"""
Pytest Configuration and Fixtures
"""

import os
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client():
    """FastAPI test client."""
    app = create_app()
    return TestClient(app)


@pytest.fixture
def sample_email_headers():
    """Sample email headers for testing."""
    return [
        {"name": "From", "value": "Newsletter <newsletter@example.com>"},
        {"name": "Subject", "value": "Test Email Subject"},
        {"name": "List-Unsubscribe", "value": "<https://example.com/unsubscribe>"},
    ]


@pytest.fixture
def sample_email_headers_one_click():
    """Sample email headers with one-click unsubscribe."""
    return [
        {"name": "From", "value": "Marketing <marketing@company.com>"},
        {"name": "Subject", "value": "Special Offer"},
        {"name": "List-Unsubscribe", "value": "<https://company.com/unsub?id=123>"},
        {"name": "List-Unsubscribe-Post", "value": "List-Unsubscribe=One-Click"},
    ]


@pytest.fixture(autouse=True)
def reset_app_state():
    """Reset shared app state before/after each test.

    `state` is a module-level singleton (app/core/state.py), so without this
    a background task triggered by one test (e.g. a delete-scan storing
    filters) can leak into an unrelated test run later in the same session.
    """
    from app.core import state as app_state

    def _reset():
        app_state.reset_scan()
        app_state.reset_delete_scan()
        app_state.reset_mark_read()
        app_state.reset_delete_bulk()
        app_state.reset_download()
        app_state.reset_label_operation()
        app_state.reset_archive()
        app_state.reset_important()
        app_state.reset_archive_scan()
        app_state.reset_markread_scan()
        app_state.current_user = {"email": None, "logged_in": False}

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def mock_gmail_auth(monkeypatch):
    """Automatically mock Gmail authentication to prevent browser opening during tests."""
    # Set environment variable to disable web auth mode (prevents browser opening)
    monkeypatch.setenv("WEB_AUTH", "false")

    # Mock file existence checks for credentials to return False (no credentials)
    # This prevents OAuth flow from starting since get_gmail_service will return early
    original_exists = os.path.exists

    def mock_exists(path):
        path_str = str(path)
        if "credentials.json" in path_str or "token.json" in path_str:
            return False
        return original_exists(path)

    monkeypatch.setattr("os.path.exists", mock_exists)


@pytest.fixture(autouse=True)
def isolate_operation_log(monkeypatch, tmp_path):
    """Repoint the operation log's file path to a tmp dir for every test.

    `operation_log` derives its file path from `settings.token_file`'s
    directory (same pattern as `security.py`'s `auth.json`) - without this,
    tests would read/write whatever `token.json`'s directory resolves to on
    the developer's machine.
    """
    from app.services import operation_log

    monkeypatch.setattr(operation_log.settings, "token_file", str(tmp_path / "token.json"))


@pytest.fixture(autouse=True)
def isolate_app_password(monkeypatch):
    """Neutralize APP_PASSWORD for every test, regardless of the developer's
    local .env.

    `settings` is a singleton built once at import time, so `monkeypatch.
    setenv` alone can't affect it - a developer's local .env (e.g. one set
    up to test the login gate manually) would otherwise silently enable the
    auth gate for the whole suite and break every unauthenticated-request
    test. Tests that specifically exercise the gate set this back to a
    value themselves within the test body.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_password", None)
