"""
Tests for Auth Gate API Endpoints
------------------------------------
Tests for POST /api/login and POST /api/logout (Phase 1.3). Distinct from
Gmail OAuth sign-in/sign-out — this is the app-level shared-password gate.
"""

import pytest

from app.core import security

# client fixture is provided by conftest.py


@pytest.fixture(autouse=True)
def _isolate_auth_state():
    security._active_sessions.clear()
    security.settings.app_password = None
    yield
    security._active_sessions.clear()
    security.settings.app_password = None


class TestLogin:
    def test_login_succeeds_with_correct_password(self, client):
        security.settings.app_password = "hunter2"

        response = client.post("/api/login", json={"password": "hunter2"})

        assert response.status_code == 200
        assert response.json() == {"success": True}
        assert security.SESSION_COOKIE_NAME in response.cookies

    def test_login_fails_with_incorrect_password(self, client):
        security.settings.app_password = "hunter2"

        response = client.post("/api/login", json={"password": "wrong"})

        assert response.status_code == 401
        assert security.SESSION_COOKIE_NAME not in response.cookies

    def test_login_succeeds_when_auth_disabled(self, client):
        response = client.post("/api/login", json={"password": "anything"})
        assert response.status_code == 200

    def test_session_cookie_grants_access_to_protected_endpoints(self, client):
        security.settings.app_password = "hunter2"

        login_response = client.post("/api/login", json={"password": "hunter2"})
        assert login_response.status_code == 200

        response = client.get("/api/auth-status")
        assert response.status_code == 200


class TestLogout:
    def test_logout_clears_session(self, client):
        security.settings.app_password = "hunter2"
        client.post("/api/login", json={"password": "hunter2"})
        assert client.get("/api/auth-status").status_code == 200

        logout_response = client.post("/api/logout")

        assert logout_response.status_code == 200
        assert client.get("/api/auth-status").status_code == 401

    def test_logout_without_a_session_is_a_noop(self, client):
        response = client.post("/api/logout")
        assert response.status_code == 200
