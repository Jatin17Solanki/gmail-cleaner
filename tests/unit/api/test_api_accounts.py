"""
Tests for Accounts API Endpoints (Phase 4a: Multi-account switcher)
------------------------------------------------------------------------
Tests for GET /api/accounts, POST /api/accounts/switch, POST /api/accounts/add.
"""

from unittest.mock import patch

# client fixture is provided by conftest.py


class TestListAccountsEndpoint:
    @patch("app.api.accounts.accounts.get_active_account", return_value="a@example.com")
    @patch(
        "app.api.accounts.accounts.list_accounts",
        return_value=["a@example.com", "b@example.com"],
    )
    def test_marks_the_active_account(self, mock_list, mock_active, client):
        response = client.get("/api/accounts")

        assert response.status_code == 200
        data = response.json()
        assert data == [
            {"email": "a@example.com", "active": True},
            {"email": "b@example.com", "active": False},
        ]

    @patch("app.api.accounts.accounts.get_active_account", return_value=None)
    @patch("app.api.accounts.accounts.list_accounts", return_value=[])
    def test_empty_when_no_accounts_registered(self, mock_list, mock_active, client):
        response = client.get("/api/accounts")

        assert response.status_code == 200
        assert response.json() == []


class TestSwitchAccountEndpoint:
    @patch("app.api.accounts.switch_active_account")
    def test_switches_to_a_known_account(self, mock_switch, client):
        mock_switch.return_value = {"success": True, "message": "Switched to a@example.com"}

        response = client.post("/api/accounts/switch", json={"email": "a@example.com"})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        mock_switch.assert_called_once_with("a@example.com")

    @patch("app.api.accounts.switch_active_account")
    def test_unknown_account_reports_failure_in_body(self, mock_switch, client):
        mock_switch.return_value = {"success": False, "message": "Unknown account"}

        response = client.post("/api/accounts/switch", json={"email": "nope@example.com"})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["message"] == "Unknown account"

    def test_missing_email_is_a_validation_error(self, client):
        response = client.post("/api/accounts/switch", json={})
        assert response.status_code == 422


class TestAddAccountEndpoint:
    @patch("app.api.accounts.get_gmail_service")
    def test_starts_oauth_for_a_new_account(self, mock_get_service, client):
        mock_get_service.return_value = (
            None,
            "Sign-in started. Please complete authorization in your browser.",
        )

        response = client.post("/api/accounts/add")

        assert response.status_code == 200
        assert response.json()["status"] == "signing_in"
        # add_new_account=True must always be used here - it's what forces a
        # fresh consent flow instead of reusing the active account's creds.
        mock_get_service.assert_called_once_with(add_new_account=True)

    @patch("app.api.accounts.get_gmail_service")
    def test_surfaces_genuine_errors(self, mock_get_service, client):
        mock_get_service.return_value = (None, "credentials.json not found!")

        response = client.post("/api/accounts/add")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "error"
        assert data["error"] == "credentials.json not found!"
