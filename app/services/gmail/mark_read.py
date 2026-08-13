"""
Gmail Mark as Read Operations
------------------------------
Functions for scanning senders with unread mail and marking selected
senders' unread mail as read.
"""

from collections import defaultdict
from typing import Optional

from app.core import state
from app.services import operation_log
from app.services.auth import get_gmail_service
from app.services.gmail import quota
from app.services.gmail.helpers import build_gmail_query, get_sender_info, get_subject

# Phase 3: Mark as read gets its own sender-row list (previously just an
# aggregate unread count + a blind "mark N most recent" picker), mirroring
# scan_senders_for_delete/scan_senders_for_archive. Subjects are stored 1:1
# with message_ids, uncapped (see delete.py's equivalent comment) — Phase
# 4c's expanded-row pagination is a client-side reveal over this
# already-fetched data.


def scan_senders_for_markread(limit: int = 1000, filters: Optional[dict] = None):
    """Scan unread emails and group by sender for Mark-as-read's sender-row list."""
    if limit <= 0:
        state.reset_markread_scan()
        state.markread_scan_status["error"] = "Limit must be greater than 0"
        state.markread_scan_status["done"] = True
        return

    state.reset_markread_scan()
    state.markread_scan_filters = filters
    state.markread_scan_status["message"] = "Connecting to Gmail..."

    service, error = get_gmail_service()
    if error:
        state.markread_scan_status["error"] = error
        state.markread_scan_status["done"] = True
        return

    try:
        state.markread_scan_status["message"] = "Fetching emails..."

        # Always scoped to unread mail - that's the whole point of this view.
        query_filters = dict(filters or {})
        query_filters["unread_only"] = True
        query = build_gmail_query(query_filters)

        results = quota.execute_with_backoff(
            service.users()
            .messages()
            .list(userId="me", maxResults=min(limit, 500), q=query or None),
            quota.COST["messages.list"],
            state.markread_scan_status,
            label="messages.list (markread scan)",
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
                state.markread_scan_status,
                label="messages.list (markread scan)",
            )
            messages.extend(results.get("messages", []))

        messages = messages[:limit]
        total = len(messages)

        if total == 0:
            state.markread_scan_status["message"] = "No unread emails found"
            state.markread_scan_status["done"] = True
            return

        state.markread_scan_status["estimated_seconds"] = quota.estimate_scan_seconds(
            total
        )
        state.markread_scan_status["message"] = f"Scanning {total} emails..."

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
        # Gmail caps concurrent requests per user at 50 - a batch's
        # sub-requests fire essentially simultaneously (see quota.py's
        # MAX_CONCURRENT_BATCH_SIZE), so this must not exceed that.
        batch_size = quota.MAX_CONCURRENT_BATCH_SIZE

        def process_message(request_id, response, exception) -> None:
            nonlocal processed
            processed += 1
            state.markread_scan_status["progress"] = int(processed / total * 100)
            state.markread_scan_status["message"] = (
                f"Scanned {processed}/{total} emails"
            )

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
                sender_counts[sender_email]["subjects"].append(subject)

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
                    metadataHeaders=["From", "Subject", "Date"],
                )
            )

        quota.run_batched_gets(
            service,
            [msg_data["id"] for msg_data in messages],
            build_get_request,
            process_message,
            quota.COST["messages.get"],
            state.markread_scan_status,
            batch_size=batch_size,
            label="messages.get (markread scan)",
        )

        sorted_senders = sorted(
            [{"email": k, **v} for k, v in sender_counts.items()],
            key=lambda x: x["count"],
            reverse=True,
        )

        state.markread_scan_status["message"] = "Fetching total counts..."
        # Reuses query_filters (already carries unread_only=True) so the
        # true count stays scoped to unread mail, same as the scan itself -
        # not every message ever received from this sender.
        quota.fetch_true_sender_totals(
            service,
            sorted_senders,
            query_filters,
            state.markread_scan_status,
            label="messages.list (markread total count)",
        )

        state.markread_scan_results = sorted_senders
        state.markread_scan_status["message"] = f"Found {len(sorted_senders)} senders"
        state.markread_scan_status["done"] = True

    except Exception as e:
        state.markread_scan_status["error"] = str(e)
        state.markread_scan_status["done"] = True


def get_markread_scan_status() -> dict:
    """Get mark-as-read scan status."""
    return state.markread_scan_status.copy()


def get_markread_scan_results() -> list:
    """Get mark-as-read scan results."""
    return state.markread_scan_results.copy()


def mark_emails_as_read_bulk_background(
    senders: list[str],
    filters: Optional[dict] = None,
    excluded_message_ids: Optional[list[str]] = None,
) -> None:
    """Mark unread emails as read for selected senders (background task).

    Args:
        senders: Sender email addresses or domains.
        filters: Filters that were active in the scan that surfaced these
            senders (see build_gmail_query) — marking stays scoped to the
            filtered subset the user reviewed (#107 pattern).
        excluded_message_ids: Message IDs to leave untouched even though
            they match the sender+filters query — see delete.py's
            delete_emails_bulk_background for the query-minus-excluded
            reasoning (Phase 4c).
    """
    state.reset_mark_read()

    if not senders or not isinstance(senders, list):
        state.mark_read_status["done"] = True
        state.mark_read_status["error"] = "No senders specified"
        return

    state.mark_read_status["total_senders"] = len(senders)
    state.mark_read_status["message"] = "Starting..."

    service, error = get_gmail_service()
    if error:
        state.mark_read_status["done"] = True
        state.mark_read_status["error"] = error
        return

    marked_ids: list[str] = []
    try:
        total_marked = 0
        excluded = set(excluded_message_ids) if excluded_message_ids else None

        for i, sender in enumerate(senders):
            state.mark_read_status["current_sender"] = i + 1
            state.mark_read_status["message"] = f"Marking emails from {sender}..."
            state.mark_read_status["progress"] = int((i / len(senders)) * 100)

            query_filters = dict(filters or {})
            query_filters["sender"] = sender
            query_filters["unread_only"] = True
            query = build_gmail_query(query_filters)
            message_ids = []
            page_token = None

            while True:
                result = quota.execute_with_backoff(
                    service.users()
                    .messages()
                    .list(userId="me", q=query, maxResults=500, pageToken=page_token),
                    quota.COST["messages.list"],
                    state.mark_read_status,
                )

                messages = result.get("messages", [])
                message_ids.extend([m["id"] for m in messages])

                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            if excluded:
                message_ids = [m for m in message_ids if m not in excluded]

            if not message_ids:
                continue

            for j in range(0, len(message_ids), 100):
                batch = message_ids[j : j + 100]
                quota.execute_with_backoff(
                    service.users()
                    .messages()
                    .batchModify(
                        userId="me", body={"ids": batch, "removeLabelIds": ["UNREAD"]}
                    ),
                    quota.COST["messages.batchModify"],
                    state.mark_read_status,
                )
                total_marked += len(batch)
                marked_ids.extend(batch)
                state.mark_read_status["marked_count"] = total_marked

        state.mark_read_status["progress"] = 100
        state.mark_read_status["done"] = True
        state.mark_read_status["marked_count"] = total_marked
        state.mark_read_status["message"] = (
            f"Marked {total_marked} emails from {len(senders)} senders as read"
        )

    except Exception as e:
        state.mark_read_status["error"] = str(e)
        state.mark_read_status["done"] = True
    finally:
        # Log whatever actually succeeded, even if a later batch raised —
        # restoring must only ever cover messages truly modified.
        if marked_ids:
            operation_log.append_entry(
                action_type="mark_read",
                message_ids=marked_ids,
                added_labels=[],
                removed_labels=["UNREAD"],
                summary={"senders": senders},
                account_email=state.current_user.get("email"),
            )
        # Remove fully-marked senders from the cached scan results, same
        # pattern delete/archive use for their scan results.
        state.markread_scan_results = [
            r for r in state.markread_scan_results if r.get("email") not in senders
        ]


def get_mark_read_status() -> dict:
    """Get mark-as-read bulk operation status."""
    return state.mark_read_status.copy()
