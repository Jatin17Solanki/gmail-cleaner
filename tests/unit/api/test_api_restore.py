"""
Tests for Restore API Endpoints
----------------------------------
Tests for GET /api/restore and POST /api/restore/{entry_id}.
"""

from unittest.mock import patch

# client fixture is provided by conftest.py


class TestListRestorableEndpoint:
    @patch("app.api.restore.operation_log.list_entries")
    def test_returns_mapped_entries(self, mock_list, client):
        mock_list.return_value = [
            {
                "id": "abc123",
                "action_type": "delete",
                "timestamp": "2026-08-10T12:00:00+00:00",
                "source": "manual",
                "message_ids": ["m1", "m2"],
                "added_labels": ["TRASH"],
                "removed_labels": [],
                "summary": {"senders": ["a@example.com"]},
            }
        ]

        response = client.get("/api/restore")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == "abc123"
        assert data[0]["action_type"] == "delete"
        assert data[0]["message_count"] == 2
        assert data[0]["senders"] == ["a@example.com"]
        assert data[0]["label_name"] is None

    @patch("app.api.restore.operation_log.list_entries")
    def test_maps_label_name_from_summary(self, mock_list, client):
        mock_list.return_value = [
            {
                "id": "abc123",
                "action_type": "label_add",
                "timestamp": "2026-08-10T12:00:00+00:00",
                "source": "manual",
                "message_ids": ["m1"],
                "added_labels": ["Label_1"],
                "removed_labels": [],
                "summary": {"senders": ["a@example.com"], "label_name": "Security"},
            }
        ]

        response = client.get("/api/restore")

        assert response.json()[0]["label_name"] == "Security"

    @patch("app.api.restore.operation_log.list_entries")
    def test_empty_list(self, mock_list, client):
        mock_list.return_value = []

        response = client.get("/api/restore")

        assert response.status_code == 200
        assert response.json() == []


class TestRestoreEndpoint:
    @patch("app.api.restore.restore_operation")
    def test_successful_restore(self, mock_restore, client):
        mock_restore.return_value = {
            "success": True,
            "restored": 2,
            "message": "Restored 2 messages",
        }

        response = client.post("/api/restore/abc123")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["restored"] == 2
        mock_restore.assert_called_once_with("abc123")

    @patch("app.api.restore.restore_operation")
    def test_unknown_entry_reports_failure_in_body(self, mock_restore, client):
        mock_restore.return_value = {
            "success": False,
            "restored": 0,
            "message": "Entry not found",
        }

        response = client.post("/api/restore/does-not-exist")

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is False
        assert data["message"] == "Entry not found"
