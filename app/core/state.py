"""
Global Application State
------------------------
Shared state across the application.
"""

import threading
from typing import Optional


class AppState:
    """Global application state container."""

    def __init__(self) -> None:
        # User state
        self.current_user: dict = {"email": None, "logged_in": False}

        # Scan state
        self.scan_results: list = []
        self.scan_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
        }

        # Mark read state
        self.mark_read_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "marked_count": 0,
        }

        # Delete state
        self.delete_scan_results: list = []
        self.delete_scan_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
        }
        # Filters active in the most recent delete-scan (#107) — delete and
        # label operations on the resulting senders reuse these so they only
        # affect the subset of messages the user actually reviewed.
        self.delete_scan_filters: Optional[dict] = None

        # Delete bulk operation state
        self.delete_bulk_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "deleted_count": 0,
            "total_senders": 0,
            "current_sender": 0,
        }

        # Download emails state
        self.download_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "total_emails": 0,
            "fetched_count": 0,
            "csv_data": None,
        }

        # Auth state
        self.pending_auth_url: dict = {"url": None}
        self.pending_auth_code: dict = {"code": None}
        self.oauth_state: dict = {"state": None}
        self.oauth_state_lock: threading.Lock = threading.Lock()

        # Label operation state
        self.label_operation_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "affected_count": 0,
            "total_senders": 0,
            "current_sender": 0,
        }

        # Archive state
        self.archive_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "archived_count": 0,
            "total_senders": 0,
            "current_sender": 0,
        }

        # Mark important state
        self.important_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "affected_count": 0,
            "total_senders": 0,
            "current_sender": 0,
        }

        # Archive scan state (Phase 3 — Archive gets its own tab/scan,
        # mirroring delete_scan_*, instead of reusing Delete's results)
        self.archive_scan_results: list = []
        self.archive_scan_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
        }
        self.archive_scan_filters: Optional[dict] = None

        # Mark-as-read scan state (Phase 3 — sender-list view, mirroring
        # delete_scan_*, replacing the old blind "mark N unread" flow)
        self.markread_scan_results: list = []
        self.markread_scan_status: dict = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
        }
        self.markread_scan_filters: Optional[dict] = None

    def reset_scan(self):
        """Reset scan state."""
        self.scan_results = []
        self.scan_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
        }

    def reset_delete_scan(self):
        """Reset delete scan state."""
        self.delete_scan_results = []
        self.delete_scan_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "estimated_seconds": None,
        }
        self.delete_scan_filters = None

    def reset_mark_read(self):
        """Reset mark read state."""
        self.mark_read_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "marked_count": 0,
        }

    def reset_delete_bulk(self):
        """Reset delete bulk state."""
        self.delete_bulk_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "deleted_count": 0,
            "total_senders": 0,
            "current_sender": 0,
        }

    def reset_download(self):
        """Reset download state."""
        self.download_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "total_emails": 0,
            "fetched_count": 0,
            "csv_data": None,
        }

    def reset_label_operation(self):
        """Reset label operation state."""
        self.label_operation_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "affected_count": 0,
            "total_senders": 0,
            "current_sender": 0,
        }

    def reset_archive(self):
        """Reset archive state."""
        self.archive_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "archived_count": 0,
            "total_senders": 0,
            "current_sender": 0,
        }

    def reset_important(self):
        """Reset mark important state."""
        self.important_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "affected_count": 0,
            "total_senders": 0,
            "current_sender": 0,
        }

    def reset_archive_scan(self):
        """Reset archive scan state."""
        self.archive_scan_results = []
        self.archive_scan_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "estimated_seconds": None,
        }
        self.archive_scan_filters = None

    def reset_markread_scan(self):
        """Reset mark-as-read scan state."""
        self.markread_scan_results = []
        self.markread_scan_status = {
            "progress": 0,
            "message": "Ready",
            "done": False,
            "error": None,
            "estimated_seconds": None,
        }
        self.markread_scan_filters = None


# Global state instance
state = AppState()
