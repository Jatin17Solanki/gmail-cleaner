"""Services module exports."""

from .auth import (
    get_gmail_service,
    sign_out,
    check_login_status,
    get_web_auth_status,
    is_web_auth_mode,
    needs_auth_setup,
)

from .gmail import (
    # Filters
    build_gmail_query,
    # Unsubscribe
    unsubscribe_single,
    # Mark as read
    scan_senders_for_markread,
    get_markread_scan_status,
    get_markread_scan_results,
    mark_emails_as_read_bulk_background,
    get_mark_read_status,
    # Delete
    scan_senders_for_delete,
    get_delete_scan_status,
    get_delete_scan_results,
    delete_emails_by_sender,
    delete_emails_bulk,
    delete_emails_bulk_background,
    get_delete_bulk_status,
    # Download
    download_emails_background,
    get_download_status,
    get_download_csv,
    # Labels
    get_labels,
    create_label,
    delete_label,
    apply_label_to_senders_background,
    remove_label_from_senders_background,
    get_label_operation_status,
    # Archive
    scan_senders_for_archive,
    get_archive_scan_status,
    get_archive_scan_results,
    archive_emails_background,
    get_archive_status,
    # Mark Important
    mark_important_background,
    get_important_status,
    # Restore
    restore_operation,
)
from . import operation_log
