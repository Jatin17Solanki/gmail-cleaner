"""
Gmail Service Module
---------------------
Core Gmail operations: scanning, unsubscribing, marking read, deleting.

This module is split into multiple files for better organization:
- helpers.py: Security, filters, and email parsing utilities
- unsubscribe.py: Unsubscribe operations
- mark_read.py: Mark-as-read scan + mark-as-read operations
- delete.py: Delete scan + delete operations (Unsubscribe is a per-row
  action here too, Phase 3 - see PROGRESS.md)
- download.py: Email download operations
- labels.py: Label management operations
- archive.py: Archive scan + archive operations
- important.py: Mark important operations
- restore.py: Restore operations (reverses an operation-log entry)
- routines.py: Preview/run a saved Routine (app/services/routines.py holds
  its CRUD storage)
"""

# Import all functions for backward compatibility
from app.services.auth import get_gmail_service
from app.services.gmail.helpers import (
    build_gmail_query,
    validate_unsafe_url,
    get_unsubscribe_from_headers,
    get_sender_info,
    get_subject,
)
from app.services.gmail.unsubscribe import (
    unsubscribe_single,
)
from app.services.gmail.mark_read import (
    scan_senders_for_markread,
    get_markread_scan_status,
    get_markread_scan_results,
    mark_emails_as_read_bulk_background,
    get_mark_read_status,
)
from app.services.gmail.delete import (
    scan_senders_for_delete,
    get_delete_scan_status,
    get_delete_scan_results,
    delete_emails_by_sender,
    delete_emails_bulk,
    delete_emails_bulk_background,
    get_delete_bulk_status,
)
from app.services.gmail.download import (
    download_emails_background,
    get_download_status,
    get_download_csv,
)
from app.services.gmail.labels import (
    get_labels,
    create_label,
    delete_label,
    apply_label_to_senders_background,
    remove_label_from_senders_background,
    get_label_operation_status,
)
from app.services.gmail.archive import (
    scan_senders_for_archive,
    get_archive_scan_status,
    get_archive_scan_results,
    archive_emails_background,
    get_archive_status,
)
from app.services.gmail.important import (
    mark_important_background,
    get_important_status,
)
from app.services.gmail.restore import (
    restore_operation,
)
from app.services.gmail.routines import (
    preview_routine,
    run_routine_background,
    get_routine_run_status,
)

# Export private helper functions with underscore-prefixed aliases for backward compatibility.
# These are used by tests that import the original function names from this module.
_get_unsubscribe_from_headers = get_unsubscribe_from_headers
_get_sender_info = get_sender_info
_get_subject = get_subject

# Export all public functions
__all__ = [
    # Archive
    "scan_senders_for_archive",
    "get_archive_scan_status",
    "get_archive_scan_results",
    "archive_emails_background",
    "get_archive_status",
    # Auth (for backward compatibility)
    "get_gmail_service",
    # Delete
    "delete_emails_bulk",
    "delete_emails_bulk_background",
    "delete_emails_by_sender",
    "get_delete_bulk_status",
    "get_delete_scan_results",
    "get_delete_scan_status",
    "scan_senders_for_delete",
    # Download
    "download_emails_background",
    "get_download_csv",
    "get_download_status",
    # Helpers
    "build_gmail_query",
    "validate_unsafe_url",
    # Important
    "get_important_status",
    "mark_important_background",
    # Labels
    "apply_label_to_senders_background",
    "create_label",
    "delete_label",
    "get_label_operation_status",
    "get_labels",
    "remove_label_from_senders_background",
    # Mark as read
    "scan_senders_for_markread",
    "get_markread_scan_status",
    "get_markread_scan_results",
    "mark_emails_as_read_bulk_background",
    "get_mark_read_status",
    # Restore
    "restore_operation",
    # Routines
    "preview_routine",
    "run_routine_background",
    "get_routine_run_status",
    # Private helpers (for testing)
    "_get_sender_info",
    "_get_subject",
    "_get_unsubscribe_from_headers",
    # Unsubscribe
    "unsubscribe_single",
]
