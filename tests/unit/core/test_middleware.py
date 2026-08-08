"""
Tests for AuthGateMiddleware
------------------------------
Verifies every route is gated once APP_PASSWORD is configured (Phase 1.3,
resolves #109/#108/#111), and that the gate is a no-op when it isn't.
"""

import pytest
from fastapi.testclient import TestClient

from app.core import security
from app.main import create_app


@pytest.fixture
def client():
    """Local override of the conftest client: redirects must not be followed
    automatically, since we assert on the redirect response itself."""
    app = create_app()
    return TestClient(app, follow_redirects=False)


@pytest.fixture(autouse=True)
def _isolate_auth_state():
    security._active_sessions.clear()
    security.settings.app_password = None
    yield
    security._active_sessions.clear()
    security.settings.app_password = None


class TestAuthDisabled:
    """Default state in this repo/test env: no APP_PASSWORD configured."""

    def test_root_accessible_without_session(self, client):
        response = client.get("/")
        assert response.status_code == 200

    def test_api_endpoint_accessible_without_session(self, client):
        response = client.get("/api/auth-status")
        assert response.status_code == 200


class TestAuthEnabled:
    def test_root_redirects_to_login_without_session(self, client):
        security.settings.app_password = "hunter2"
        response = client.get("/")
        assert response.status_code in (302, 307)
        assert response.headers["location"].startswith("/login")

    def test_api_endpoint_returns_401_without_session(self, client):
        security.settings.app_password = "hunter2"
        response = client.get("/api/auth-status")
        assert response.status_code == 401

    def test_login_page_accessible_without_session(self, client):
        security.settings.app_password = "hunter2"
        response = client.get("/login")
        assert response.status_code == 200

    def test_static_assets_accessible_without_session(self, client):
        security.settings.app_password = "hunter2"
        response = client.get("/static/css/base.css")
        assert response.status_code == 200

    def test_valid_session_grants_access(self, client):
        security.settings.app_password = "hunter2"
        token = security.create_session()
        client.cookies.set(security.SESSION_COOKIE_NAME, token)

        response = client.get("/")

        assert response.status_code == 200

    def test_invalid_session_cookie_is_rejected(self, client):
        security.settings.app_password = "hunter2"
        client.cookies.set(security.SESSION_COOKIE_NAME, "forged-token")

        response = client.get("/api/auth-status")

        assert response.status_code == 401
