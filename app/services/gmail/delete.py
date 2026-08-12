"""
Gmail Delete Operations
-----------------------
Functions for deleting emails and scanning senders.
"""

import logging
import re
from collections import defaultdict
from typing import Optional

from app.core import state
from app.services import operation_log
from app.services.auth import get_gmail_service
from app.services.gmail import quota
from app.services.gmail.helpers import (
    build_gmail_query,
    get_sender_info,
    get_subject,
    get_unsubscribe_from_headers,
)

# Phase 3: Unsubscribe is a per-row action on the merged Delete view, not
# its own tab. Subjects are stored 1:1 with message_ids for every scanned
# message of a sender (no cap) — messages.get() already ran for each one
# during the scan, so this costs nothing extra; the ~20-per-page reveal in
# the expanded-row UI (Phase 4c) is a client-side pagination choice over
# already-fetched data, not a data-fetching cap.

logger = logging.getLogger(__name__)


def scan_senders_for_delete(limit: int = 1000, filters: Optional[dict] = None):
    """Scan emails and group by sender for bulk delete."""
    # Validate input
    if limit <= 0:
        state.reset_delete_scan()
        state.delete_scan_status["error"] = "Limit must be greater than 0"
        state.delete_scan_status["done"] = True
        return

    state.reset_delete_scan()
    state.delete_scan_filters = filters
    state.delete_scan_status["message"] = "Connecting to Gmail..."

    service, error = get_gmail_service()
    if error:
        state.delete_scan_status["error"] = error
        state.delete_scan_status["done"] = True
        return

    try:
        state.delete_scan_status["message"] = "Fetching emails..."

        query = build_gmail_query(filters)

        results = quota.execute_with_backoff(
            service.users()
            .messages()
            .list(userId="me", maxResults=min(limit, 500), q=query or None),
            quota.COST["messages.list"],
            state.delete_scan_status,
            label="messages.list (delete scan)",
        )

        messages = results.get("messages", [])

        while "nextPageToken" in results and len(messages) < limit:
            results = quota.execute_with_backoff(
                service.users()
                .messages()
                .list(
                    userId="me",
                    maxResults=min(limit - len(messages), 500),
                    pageToken=results["nextPageToken"],
                    q=query or None,
                ),
                quota.COST["messages.list"],
                state.delete_scan_status,
                label="messages.list (delete scan)",
            )
            messages.extend(results.get("messages", []))

        messages = messages[:limit]
        total = len(messages)

        if total == 0:
            state.delete_scan_status["message"] = "No emails found"
            state.delete_scan_status["done"] = True
            return

        state.delete_scan_status["estimated_seconds"] = quota.estimate_scan_seconds(
            total
        )
        state.delete_scan_status["message"] = f"Scanning {total} emails..."

        # Group by sender using Gmail Batch API
        sender_counts: dict[str, dict] = defaultdict(
            lambda: {
                "count": 0,
                "sender": "",
                "email": "",
                "subjects": [],
                "first_date": None,
                "last_date": None,
                "message_ids": [],
                "total_size": 0,
                "unsubscribe_link": None,
                "unsubscribe_type": None,
            }
        )
        processed = 0
        # Gmail caps concurrent requests per user at 50 - a batch's
        # sub-requests fire essentially simultaneously (see quota.py's
        # MAX_CONCURRENT_BATCH_SIZE), so this must not exceed that.
        batch_size = quota.MAX_CONCURRENT_BATCH_SIZE

        def process_message(request_id, response, exception) -> None:
            nonlocal processed
            processed += 1
            state.delete_scan_status["progress"] = int(processed / total * 100)
            state.delete_scan_status["message"] = f"Scanned {processed}/{total} emails"

            if exception:
                return

            headers = response.get("payload", {}).get("headers", [])
            sender_name, sender_email = get_sender_info(headers)
            subject = get_subject(headers)
            msg_id = response.get("id", "")
            size_estimate = response.get("sizeEstimate", 0)
            unsub_link, unsub_type = get_unsubscribe_from_headers(headers)

            # Extract date from headers
            email_date = None
            for header in headers:
                if header["name"].lower() == "date":
                    email_date = header["value"]
                    break

            if sender_email:
                sender_counts[sender_email]["count"] += 1
                sender_counts[sender_email]["sender"] = sender_name
                sender_counts[sender_email]["email"] = sender_email
                sender_counts[sender_email]["message_ids"].append(msg_id)
                sender_counts[sender_email]["total_size"] += size_estimate
                sender_counts[sender_email]["subjects"].append(subject)
                if unsub_link:
                    sender_counts[sender_email]["unsubscribe_link"] = unsub_link
                    sender_counts[sender_email]["unsubscribe_type"] = unsub_type

                # Track first and last dates
                if email_date:
                    if sender_counts[sender_email]["first_date"] is None:
                        sender_counts[sender_email]["first_date"] = email_date
                    sender_counts[sender_email]["last_date"] = email_date

        def build_get_request(msg_id: str):
            return (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=[
                        "From",
                        "Subject",
                        "Date",
                        "List-Unsubscribe",
                        "List-Unsubscribe-Post",
                    ],
                )
            )

        quota.run_batched_gets(
            service,
            [msg_data["id"] for msg_data in messages],
            build_get_request,
            process_message,
            quota.COST["messages.get"],
            state.delete_scan_status,
            batch_size=batch_size,
            label="messages.get (delete scan)",
        )

        # Sort by count
        sorted_senders = sorted(
            [{"email": k, **v} for k, v in sender_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        state.delete_scan_status["message"] = "Fetching total counts..."
        quota.fetch_true_sender_totals(
            service,
            sorted_senders,
            filters,
            state.delete_scan_status,
            label="messages.list (delete total count)",
        )

        state.delete_scan_results = sorted_senders
        state.delete_scan_status["message"] = f"Found {len(sorted_senders)} senders"
        state.delete_scan_status["done"] = True

    except Exception as e:
        state.delete_scan_status["error"] = str(e)
        state.delete_scan_status["done"] = True


def get_delete_scan_status() -> dict:
    """Get delete scan status."""
    return state.delete_scan_status.copy()


def get_delete_scan_results() -> list:
    """Get delete scan results."""
    return state.delete_scan_results.copy()


def delete_emails_by_sender(sender: str, filters: Optional[dict] = None) -> dict:
    """Delete emails from a specific sender, scoped to the given filters.

    Args:
        sender: Sender email address or domain.
        filters: Filters that were active in the scan that surfaced this
            sender (see build_gmail_query). Ensures delete only affects the
            filtered subset the user reviewed, not every message from the
            sender (#107).
    """
    if not sender or not sender.strip():
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "No sender specified",
        }

    # Validate sender format - must be a valid email address or domain
    sender = sender.strip()
    # Email format: user@domain.tld
    email_pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    # Domain format: domain.tld (at least one dot, valid domain structure)
    domain_pattern = r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$"

    if not (re.match(email_pattern, sender) or re.match(domain_pattern, sender)):
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "Invalid sender format. Must be a valid email address or domain.",
        }

    # Get size info from cached results before deleting
    size_freed = 0
    for r in state.delete_scan_results:
        if r.get("email") == sender:
            size_freed = r.get("total_size", 0)
            break

    service, error = get_gmail_service()
    if error:
        return {"success": False, "deleted": 0, "size_freed": 0, "message": error}

    deleted_ids: list[str] = []
    try:
        # Find all emails from sender, scoped to the active scan filters
        query_filters = dict(filters or {})
        query_filters["sender"] = sender
        query = build_gmail_query(query_filters)
        results = quota.execute_with_backoff(
            service.users().messages().list(userId="me", q=query, maxResults=500),
            quota.COST["messages.list"],
        )
        messages = results.get("messages", [])

        while "nextPageToken" in results:
            results = quota.execute_with_backoff(
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=500,
                    pageToken=results["nextPageToken"],
                ),
                quota.COST["messages.list"],
            )
            messages.extend(results.get("messages", []))

        if not messages:
            return {
                "success": True,
                "deleted": 0,
                "size_freed": 0,
                "message": "No emails found",
            }

        # Batch delete (move to trash)
        ids = [msg["id"] for msg in messages]
        batch_size = 100
        deleted = 0

        for i in range(0, len(ids), batch_size):
            batch = ids[i : i + batch_size]
            quota.execute_with_backoff(
                service.users()
                .messages()
                .batchModify(
                    userId="me",
                    body={
                        "ids": batch,
                        "addLabelIds": ["TRASH"],
                        "removeLabelIds": ["INBOX"],
                    },
                ),
                quota.COST["messages.batchModify"],
            )
            deleted += len(batch)
            deleted_ids.extend(batch)

        # Remove sender from cached results
        state.delete_scan_results = [
            r for r in state.delete_scan_results if r.get("email") != sender
        ]

        return {
            "success": True,
            "deleted": deleted,
            "size_freed": size_freed,
            "message": f"Moved {deleted} emails to trash",
        }

    except Exception as e:
        return {"success": False, "deleted": 0, "size_freed": 0, "message": str(e)}
    finally:
        # Log whatever actually succeeded, even if a later batch raised —
        # restoring must only ever cover messages truly modified.
        if deleted_ids:
            operation_log.append_entry(
                action_type="delete",
                message_ids=deleted_ids,
                added_labels=["TRASH"],
                removed_labels=["INBOX"],
                summary={"senders": [sender]},
                account_email=state.current_user.get("email"),
            )


def delete_emails_bulk(senders: list[str], filters: Optional[dict] = None) -> dict:
    """Delete emails from multiple senders, scoped to the given filters."""
    if not senders:
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "No senders specified",
        }

    total_deleted = 0
    total_size_freed = 0
    errors = []

    for sender in senders:
        result = delete_emails_by_sender(sender, filters)
        if result["success"]:
            total_deleted += result["deleted"]
            total_size_freed += result.get("size_freed", 0)
        else:
            errors.append(f"{sender}: {result['message']}")

    # Note: delete_emails_by_sender already removes each sender from cached results

    if errors:
        return {
            "success": len(errors) < len(senders),
            "deleted": total_deleted,
            "size_freed": total_size_freed,
            "message": f"Deleted {total_deleted} emails. Errors: {'; '.join(errors[:3])}",
        }

    if total_deleted == 0:
        return {
            "success": False,
            "deleted": 0,
            "size_freed": 0,
            "message": "No emails found to delete",
        }
    return {
        "success": True,
        "deleted": total_deleted,
        "size_freed": total_size_freed,
        "message": f"Deleted {total_deleted} emails",
    }


def delete_emails_bulk_background(
    senders: list[str],
    filters: Optional[dict] = None,
    excluded_message_ids: Optional[list[str]] = None,
) -> None:
    """Delete emails from multiple senders with progress updates (background task).

    Optimized to collect all message IDs first, then batch delete in larger chunks.

    Args:
        senders: Sender email addresses or domains.
        filters: Filters that were active in the scan that surfaced these
            senders (see build_gmail_query) — deletion stays scoped to the
            filtered subset the user reviewed (#107).
        excluded_message_ids: Message IDs to leave untouched even though
            they match the sender+filters query — the per-message checkboxes
            in an expanded sender row (Phase 4c). Query-minus-excluded, not
            an include-list: everything matching still gets deleted except
            what was explicitly unchecked, so mail beyond what happened to
            be previewed isn't silently skipped.
    """
    state.reset_delete_bulk()

    # Validate input
    if not senders or not isinstance(senders, list):
        state.delete_bulk_status["done"] = True
        state.delete_bulk_status["error"] = "No senders specified"
        return

    total_senders = len(senders)
    state.delete_bulk_status["total_senders"] = total_senders
    state.delete_bulk_status["message"] = "Collecting emails to delete..."

    service, error = get_gmail_service()
    if error:
        state.delete_bulk_status["done"] = True
        state.delete_bulk_status["error"] = error
        return

    # Phase 1: Collect all message IDs from all senders
    all_message_ids = []
    errors = []

    for i, sender in enumerate(senders):
        state.delete_bulk_status["current_sender"] = i + 1
        state.delete_bulk_status["progress"] = int(
            (i / total_senders) * 40
        )  # 0-40% for collecting
        state.delete_bulk_status["message"] = f"Finding emails from {sender}..."

        try:
            query_filters = dict(filters or {})
            query_filters["sender"] = sender
            query = build_gmail_query(query_filters)
            results = quota.execute_with_backoff(
                service.users().messages().list(userId="me", q=query, maxResults=500),
                quota.COST["messages.list"],
                state.delete_bulk_status,
            )
            messages = results.get("messages", [])

            while "nextPageToken" in results:
                results = quota.execute_with_backoff(
                    service.users()
                    .messages()
                    .list(
                        userId="me",
                        q=query,
                        maxResults=500,
                        pageToken=results["nextPageToken"],
                    ),
                    quota.COST["messages.list"],
                    state.delete_bulk_status,
                )
                messages.extend(results.get("messages", []))

            all_message_ids.extend([msg["id"] for msg in messages])
        except Exception as e:
            errors.append(f"{sender}: {str(e)}")

    if excluded_message_ids:
        excluded = set(excluded_message_ids)
        all_message_ids = [m for m in all_message_ids if m not in excluded]

    if not all_message_ids:
        state.delete_bulk_status["progress"] = 100
        state.delete_bulk_status["done"] = True
        state.delete_bulk_status["message"] = "No emails found to delete"
        return

    # Phase 2: Batch delete all collected IDs (larger batches = fewer API calls)
    total_emails = len(all_message_ids)
    state.delete_bulk_status["message"] = f"Deleting {total_emails} emails..."

    batch_size = 1000  # Gmail allows up to 1000 per batchModify
    deleted = 0
    deleted_ids = []

    try:
        for i in range(0, total_emails, batch_size):
            batch = all_message_ids[i : i + batch_size]
            quota.execute_with_backoff(
                service.users()
                .messages()
                .batchModify(
                    userId="me",
                    body={
                        "ids": batch,
                        "addLabelIds": ["TRASH"],
                        "removeLabelIds": ["INBOX"],
                    },
                ),
                quota.COST["messages.batchModify"],
                state.delete_bulk_status,
            )
            deleted += len(batch)
            deleted_ids.extend(batch)
            state.delete_bulk_status["deleted_count"] = deleted
            # Progress: 40-100% for deleting
            state.delete_bulk_status["progress"] = 40 + int(
                (deleted / total_emails) * 60
            )
            state.delete_bulk_status["message"] = (
                f"Deleted {deleted}/{total_emails} emails..."
            )
    except Exception as e:
        errors.append(f"Batch delete error: {str(e)}")

    if deleted_ids:
        operation_log.append_entry(
            action_type="delete",
            message_ids=deleted_ids,
            added_labels=["TRASH"],
            removed_labels=["INBOX"],
            summary={"senders": senders},
            account_email=state.current_user.get("email"),
        )

    # Remove deleted senders from cached scan results
    state.delete_scan_results = [
        r for r in state.delete_scan_results if r.get("email") not in senders
    ]

    # Done
    state.delete_bulk_status["progress"] = 100
    state.delete_bulk_status["done"] = True
    state.delete_bulk_status["deleted_count"] = deleted

    if errors:
        state.delete_bulk_status["error"] = f"Some errors: {'; '.join(errors[:3])}"
        state.delete_bulk_status["message"] = (
            f"Deleted {deleted} emails with some errors"
        )
    else:
        state.delete_bulk_status["message"] = f"Successfully deleted {deleted} emails"


def get_delete_bulk_status() -> dict:
    """Get delete bulk operation status."""
    return state.delete_bulk_status.copy()
