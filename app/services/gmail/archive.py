"""
Gmail Archive Operations
------------------------
Functions for scanning and archiving emails (removing from inbox).
"""

import time
from collections import defaultdict
from typing import Optional

from app.core import state
from app.services import operation_log
from app.services.auth import get_gmail_service
from app.services.gmail.helpers import build_gmail_query, get_sender_info, get_subject

# Phase 3: Archive gets its own tab/scan (previously it only operated on
# whatever the Delete tab had already scanned) — mirrors delete.py's
# scan_senders_for_delete, minus the unsubscribe fields Archive has no use
# for. Same ~20 subject/message-preview cap as Delete (Phase 4c).
SUBJECTS_PER_SENDER_CAP = 20


def scan_senders_for_archive(limit: int = 1000, filters: Optional[dict] = None):
    """Scan emails and group by sender for Archive's own sender-row list."""
    if limit <= 0:
        state.reset_archive_scan()
        state.archive_scan_status["error"] = "Limit must be greater than 0"
        state.archive_scan_status["done"] = True
        return

    state.reset_archive_scan()
    state.archive_scan_filters = filters
    state.archive_scan_status["message"] = "Connecting to Gmail..."

    service, error = get_gmail_service()
    if error:
        state.archive_scan_status["error"] = error
        state.archive_scan_status["done"] = True
        return

    try:
        state.archive_scan_status["message"] = "Fetching emails..."

        query = build_gmail_query(filters)

        results = (
            service.users()
            .messages()
            .list(userId="me", maxResults=min(limit, 500), q=query or None)
            .execute()
        )

        messages = results.get("messages", [])

        while "nextPageToken" in results and len(messages) < limit:
            results = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    maxResults=min(limit - len(messages), 500),
                    pageToken=results["nextPageToken"],
                    q=query or None,
                )
                .execute()
            )
            messages.extend(results.get("messages", []))

        messages = messages[:limit]
        total = len(messages)

        if total == 0:
            state.archive_scan_status["message"] = "No emails found"
            state.archive_scan_status["done"] = True
            return

        state.archive_scan_status["message"] = f"Scanning {total} emails..."

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
            }
        )
        processed = 0
        batch_size = 100

        def process_message(request_id, response, exception) -> None:
            nonlocal processed
            processed += 1

            if exception:
                return

            headers = response.get("payload", {}).get("headers", [])
            sender_name, sender_email = get_sender_info(headers)
            subject = get_subject(headers)
            msg_id = response.get("id", "")
            size_estimate = response.get("sizeEstimate", 0)

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
                if len(sender_counts[sender_email]["subjects"]) < SUBJECTS_PER_SENDER_CAP:
                    sender_counts[sender_email]["subjects"].append(subject)

                if email_date:
                    if sender_counts[sender_email]["first_date"] is None:
                        sender_counts[sender_email]["first_date"] = email_date
                    sender_counts[sender_email]["last_date"] = email_date

        for i in range(0, len(messages), batch_size):
            batch_ids = messages[i : i + batch_size]
            batch = service.new_batch_http_request(callback=process_message)

            for msg_data in batch_ids:
                batch.add(
                    service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=msg_data["id"],
                        format="metadata",
                        metadataHeaders=["From", "Subject", "Date"],
                    )
                )

            batch.execute()

            progress = int((i + len(batch_ids)) / total * 100)
            state.archive_scan_status["progress"] = progress
            state.archive_scan_status["message"] = f"Scanned {processed}/{total} emails"

            if (i // batch_size + 1) % 5 == 0:
                time.sleep(0.3)

        sorted_senders = sorted(
            [{"email": k, **v} for k, v in sender_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        state.archive_scan_results = sorted_senders
        state.archive_scan_status["message"] = f"Found {len(sorted_senders)} senders"
        state.archive_scan_status["done"] = True

    except Exception as e:
        state.archive_scan_status["error"] = str(e)
        state.archive_scan_status["done"] = True


def get_archive_scan_status() -> dict:
    """Get archive scan status."""
    return state.archive_scan_status.copy()


def get_archive_scan_results() -> list:
    """Get archive scan results."""
    return state.archive_scan_results.copy()


def archive_emails_background(senders: list[str], filters: Optional[dict] = None):
    """Archive emails from selected senders (remove INBOX label).

    Args:
        senders: Sender email addresses or domains.
        filters: Filters that were active in the scan that surfaced these
            senders (see build_gmail_query) — archiving stays scoped to the
            filtered subset the user reviewed, same #107 pattern used by
            delete/label operations.
    """
    state.reset_archive()

    # Validate input
    if not senders or not isinstance(senders, list):
        state.archive_status["done"] = True
        state.archive_status["error"] = "No senders specified"
        return

    state.archive_status["total_senders"] = len(senders)
    state.archive_status["message"] = "Starting archive..."

    archived_ids: list[str] = []
    try:
        service, error = get_gmail_service()
        if error:
            state.archive_status["error"] = error
            state.archive_status["done"] = True
            return

        total_archived = 0

        for i, sender in enumerate(senders):
            state.archive_status["current_sender"] = i + 1
            state.archive_status["message"] = f"Archiving emails from {sender}..."
            state.archive_status["progress"] = int((i / len(senders)) * 100)

            # Find all emails from this sender, scoped to the active scan
            # filters (#107) instead of a bare from:{sender} in:inbox string.
            query_filters = dict(filters or {})
            query_filters["sender"] = sender
            query = build_gmail_query(query_filters)
            message_ids = []
            page_token = None

            while True:
                result = (
                    service.users()
                    .messages()
                    .list(userId="me", q=query, maxResults=500, pageToken=page_token)
                    .execute()
                )

                messages = result.get("messages", [])
                message_ids.extend([m["id"] for m in messages])

                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            if not message_ids:
                continue

            # Archive in batches (remove INBOX label)
            for j in range(0, len(message_ids), 100):
                batch_ids = message_ids[j : j + 100]
                service.users().messages().batchModify(
                    userId="me", body={"ids": batch_ids, "removeLabelIds": ["INBOX"]}
                ).execute()
                total_archived += len(batch_ids)
                archived_ids.extend(batch_ids)

                # Throttle every 500 emails (check at 100, 600, 1100, etc.)
                if (j + 100) % 500 == 0:
                    time.sleep(0.5)

        state.archive_status["progress"] = 100
        state.archive_status["done"] = True
        state.archive_status["archived_count"] = total_archived
        state.archive_status["message"] = (
            f"Archived {total_archived} emails from {len(senders)} senders"
        )

    except Exception as e:
        state.archive_status["error"] = f"{e!s}"
        state.archive_status["done"] = True
        state.archive_status["message"] = f"Error: {e!s}"
    finally:
        # Log whatever actually succeeded, even if a later batch raised —
        # restoring must only ever cover messages truly modified.
        if archived_ids:
            operation_log.append_entry(
                action_type="archive",
                message_ids=archived_ids,
                added_labels=[],
                removed_labels=["INBOX"],
                summary={"senders": senders},
                account_email=state.current_user.get("email"),
            )
        # Remove archived senders from the cached archive-scan results, same
        # pattern delete_emails_bulk_background uses for delete_scan_results.
        state.archive_scan_results = [
            r for r in state.archive_scan_results if r.get("email") not in senders
        ]


def get_archive_status() -> dict:
    """Get archive operation status."""
    return state.archive_status.copy()
