"""
Tests for Routines API Endpoints (Phase 4b)
-----------------------------------------------
Tests for GET/POST /api/routines, DELETE /api/routines/{id},
POST /api/routines/{id}/preview, POST /api/routines/{id}/run, and
GET /api/routines/run-status.
"""

from unittest.mock import patch

# client fixture is provided by conftest.py

_SAMPLE_ROUTINE = {
    "id": "abc123",
    "account_email": "me@example.com",
    "name": "Market newsletters",
    "senders": ["a@example.com"],
    "older_than": "7d",
    "actions": ["delete"],
    "label_id": None,
    "label_name": None,
    "schedule": None,
    "created_at": "2026-08-10T12:00:00+00:00",
    "last_run_at": None,
}


class TestListRoutinesEndpoint:
    @patch("app.api.routines.accounts.get_active_account", return_value="me@example.com")
    @patch("app.api.routines.routines.list_routines")
    def test_returns_saved_routines(self, mock_list, mock_active, client):
        mock_list.return_value = [_SAMPLE_ROUTINE]

        response = client.get("/api/routines")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "abc123"
        assert data[0]["name"] == "Market newsletters"
        mock_list.assert_called_once_with(account_email="me@example.com")

    @patch("app.api.routines.accounts.get_active_account", return_value=None)
    @patch("app.api.routines.routines.list_routines", return_value=[])
    def test_empty_list(self, mock_list, mock_active, client):
        response = client.get("/api/routines")

        assert response.status_code == 200
        assert response.json() == []


class TestCreateRoutineEndpoint:
    @patch("app.api.routines.accounts.get_active_account", return_value="me@example.com")
    @patch("app.api.routines.routines.create_routine")
    def test_creates_routine_for_active_account(
        self, mock_create, mock_active, client
    ):
        mock_create.return_value = _SAMPLE_ROUTINE

        response = client.post(
            "/api/routines",
            json={
                "name": "Market newsletters",
                "senders": ["a@example.com"],
                "older_than": "7d",
                "actions": ["delete"],
            },
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Market newsletters"
        mock_create.assert_called_once_with(
            name="Market newsletters",
            senders=["a@example.com"],
            older_than="7d",
            actions=["delete"],
            account_email="me@example.com",
            label_id=None,
            label_name=None,
        )

    @patch("app.api.routines.accounts.get_active_account", return_value=None)
    def test_no_active_account_returns_400(self, mock_active, client):
        response = client.post(
            "/api/routines",
            json={
                "name": "A",
                "senders": ["a@example.com"],
                "older_than": "7d",
                "actions": ["delete"],
            },
        )

        assert response.status_code == 400

    @patch("app.api.routines.accounts.get_active_account", return_value="me@example.com")
    @patch("app.api.routines.get_labels")
    @patch("app.api.routines.routines.create_routine")
    def test_resolves_label_name_when_label_action_selected(
        self, mock_create, mock_get_labels, mock_active, client
    ):
        mock_get_labels.return_value = {
            "success": True,
            "system_labels": [],
            "user_labels": [{"id": "Label_1", "name": "Security"}],
        }
        mock_create.return_value = _SAMPLE_ROUTINE

        client.post(
            "/api/routines",
            json={
                "name": "A",
                "senders": ["a@example.com"],
                "older_than": "7d",
                "actions": ["label"],
                "label_id": "Label_1",
            },
        )

        mock_create.assert_called_once_with(
            name="A",
            senders=["a@example.com"],
            older_than="7d",
            actions=["label"],
            account_email="me@example.com",
            label_id="Label_1",
            label_name="Security",
        )

    def test_missing_senders_returns_422(self, client):
        response = client.post(
            "/api/routines",
            json={"name": "A", "senders": [], "older_than": "7d", "actions": ["delete"]},
        )
        assert response.status_code == 422

    def test_invalid_action_returns_422(self, client):
        response = client.post(
            "/api/routines",
            json={
                "name": "A",
                "senders": ["a@example.com"],
                "older_than": "7d",
                "actions": ["unsubscribe"],
            },
        )
        assert response.status_code == 422

    def test_invalid_older_than_returns_422(self, client):
        response = client.post(
            "/api/routines",
            json={
                "name": "A",
                "senders": ["a@example.com"],
                "older_than": "1week",
                "actions": ["delete"],
            },
        )
        assert response.status_code == 422

    def test_label_action_without_label_id_returns_422(self, client):
        response = client.post(
            "/api/routines",
            json={
                "name": "A",
                "senders": ["a@example.com"],
                "older_than": "7d",
                "actions": ["label"],
            },
        )
        assert response.status_code == 422


class TestDeleteRoutineEndpoint:
    @patch("app.api.routines.accounts.get_active_account", return_value="me@example.com")
    @patch("app.api.routines.routines.delete_routine")
    def test_deletes_existing_routine(self, mock_delete, mock_active, client):
        mock_delete.return_value = _SAMPLE_ROUTINE

        response = client.delete("/api/routines/abc123")

        assert response.status_code == 200
        assert response.json()["success"] is True
        mock_delete.assert_called_once_with("abc123", account_email="me@example.com")

    @patch("app.api.routines.accounts.get_active_account", return_value="me@example.com")
    @patch("app.api.routines.routines.delete_routine", return_value=None)
    def test_unknown_routine_returns_404(self, mock_delete, mock_active, client):
        response = client.delete("/api/routines/does-not-exist")

        assert response.status_code == 404


class TestPreviewRoutineEndpoint:
    @patch("app.api.routines.preview_routine")
    def test_returns_preview(self, mock_preview, client):
        mock_preview.return_value = {
            "routine_id": "abc123",
            "name": "Market newsletters",
            "total": 3,
            "per_sender": [{"sender": "a@example.com", "count": 3}],
            "actions": ["delete"],
        }

        response = client.post("/api/routines/abc123/preview")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert data["per_sender"][0]["sender"] == "a@example.com"

    @patch("app.api.routines.preview_routine", return_value=None)
    def test_unknown_routine_returns_404(self, mock_preview, client):
        response = client.post("/api/routines/does-not-exist/preview")

        assert response.status_code == 404

    @patch("app.api.routines.preview_routine", side_effect=RuntimeError("Not authenticated"))
    def test_auth_error_returns_500(self, mock_preview, client):
        response = client.post("/api/routines/abc123/preview")

        assert response.status_code == 500


class TestRunRoutineEndpoint:
    @patch("app.api.routines.run_routine_background")
    @patch("app.api.routines.accounts.get_active_account", return_value="me@example.com")
    @patch("app.api.routines.routines.get_routine", return_value=_SAMPLE_ROUTINE)
    def test_starts_background_run(
        self, mock_get_routine, mock_active, mock_run, client
    ):
        response = client.post("/api/routines/abc123/run")

        assert response.status_code == 200
        assert response.json()["status"] == "started"
        mock_run.assert_called_once_with("abc123")

    @patch("app.api.routines.accounts.get_active_account", return_value="me@example.com")
    @patch("app.api.routines.routines.get_routine", return_value=None)
    def test_unknown_routine_returns_404(self, mock_get_routine, mock_active, client):
        response = client.post("/api/routines/does-not-exist/run")

        assert response.status_code == 404


class TestRoutineRunStatusEndpoint:
    @patch("app.api.routines.get_routine_run_status")
    def test_returns_status(self, mock_status, client):
        mock_status.return_value = {"progress": 50, "done": False}

        response = client.get("/api/routines/run-status")

        assert response.status_code == 200
        assert response.json()["progress"] == 50
