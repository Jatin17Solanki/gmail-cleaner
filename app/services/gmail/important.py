"""
Gmail Mark Important Operations
--------------------------------
Functions for marking/unmarking emails as important.
"""

from typing import Optional

from app.core import state
from app.services.auth import get_gmail_service
from app.services.gmail import quota
from app.services.gmail.helpers import build_gmail_query


def mark_important_background(
    senders: list[str], *, important: bool = True, filters: Optional[dict] = None
) -> None:
    """Mark/unmark emails from selected senders as important.

    Args:
        senders: Sender email addresses or domains.
        important: True to mark important, False to unmark.
        filters: Filters that were active in the scan that surfaced these
            senders (see build_gmail_query) — Important is now a per-row
            inline action across Delete/Mark-as-read/Archive (Phase 3), so
            it must stay scoped to whichever view's filters found the
            sender, same #107 pattern as delete/label/archive.
    """
    state.reset_important()

    # Validate input
    if not senders or not isinstance(senders, list):
        state.important_status["done"] = True
        state.important_status["error"] = "No senders specified"
        return

    state.important_status["total_senders"] = len(senders)
    action = "Marking" if important else "Unmarking"
    state.important_status["message"] = f"{action} as important..."

    try:
        service, error = get_gmail_service()
        if error:
            state.important_status["error"] = error
            state.important_status["done"] = True
            return

        total_affected = 0

        for i, sender in enumerate(senders):
            state.important_status["current_sender"] = i + 1
            state.important_status["message"] = f"{action} emails from {sender}..."
            state.important_status["progress"] = int((i / len(senders)) * 100)

            # Find all emails from this sender, scoped to the active scan
            # filters (#107) instead of a bare from:{sender} string.
            query_filters = dict(filters or {})
            query_filters["sender"] = sender
            query = build_gmail_query(query_filters)
            message_ids = []
            page_token = None

            while True:
                result = quota.execute_with_backoff(
                    service.users()
                    .messages()
                    .list(userId="me", q=query, maxResults=500, pageToken=page_token),
                    quota.COST["messages.list"],
                    state.important_status,
                )

                messages = result.get("messages", [])
                message_ids.extend([m["id"] for m in messages])

                page_token = result.get("nextPageToken")
                if not page_token:
                    break

            if not message_ids:
                continue

            # Mark in batches
            for j in range(0, len(message_ids), 100):
                batch_ids = message_ids[j : j + 100]
                # Gmail API requires explicit parameter names (addLabelIds or removeLabelIds)
                body = (
                    {"ids": batch_ids, "addLabelIds": ["IMPORTANT"]}
                    if important
                    else {"ids": batch_ids, "removeLabelIds": ["IMPORTANT"]}
                )
                quota.execute_with_backoff(
                    service.users().messages().batchModify(userId="me", body=body),
                    quota.COST["messages.batchModify"],
                    state.important_status,
                )
                total_affected += len(batch_ids)

        state.important_status["progress"] = 100
        state.important_status["done"] = True
        state.important_status["affected_count"] = total_affected
        action_done = "marked as important" if important else "unmarked as important"
        state.important_status["message"] = f"{total_affected} emails {action_done}"

    except Exception as e:
        state.important_status["error"] = f"{e!s}"
        state.important_status["done"] = True
        state.important_status["message"] = f"Error: {e!s}"


def get_important_status() -> dict:
    """Get mark important operation status."""
    return state.important_status.copy()
