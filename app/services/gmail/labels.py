"""
Gmail Label Management Operations
----------------------------------
Functions for managing Gmail labels.
"""

from typing import Optional

from app.core import state
from app.services import operation_log
from app.services.auth import get_gmail_service
from app.services.gmail import quota
from app.services.gmail.helpers import build_gmail_query


def get_labels() -> dict:
    """Get all Gmail labels."""
    service, error = get_gmail_service()
    if error:
        return {"success": False, "labels": [], "error": error}

    try:
        results = quota.execute_with_backoff(
            service.users().labels().list(userId="me"), quota.COST["labels.list"]
        )
        labels = results.get("labels", [])

        # Categorize labels
        system_labels = []
        user_labels = []

        for label in labels:
            label_info = {
                "id": label.get("id"),
                "name": label.get("name"),
                "type": label.get("type", "user"),
            }

            if label.get("type") == "system":
                system_labels.append(label_info)
            else:
                user_labels.append(label_info)

        # Sort user labels alphabetically
        user_labels.sort(key=lambda x: x["name"].lower())

        return {
            "success": True,
            "system_labels": system_labels,
            "user_labels": user_labels,
            "error": None,
        }
    except Exception as e:
        return {"success": False, "labels": [], "error": str(e)}


def create_label(name: str) -> dict:
    """Create a new Gmail label."""
    if not name or not name.strip():
        return {"success": False, "label": None, "error": "Label name is required"}

    service, error = get_gmail_service()
    if error:
        return {"success": False, "label": None, "error": error}

    try:
        label_body = {
            "name": name.strip(),
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }

        result = quota.execute_with_backoff(
            service.users().labels().create(userId="me", body=label_body),
            quota.COST["labels.create"],
        )

        return {
            "success": True,
            "label": {
                "id": result.get("id"),
                "name": result.get("name"),
                "type": result.get("type", "user"),
            },
            "error": None,
        }
    except Exception as e:
        error_msg = str(e)
        if "Label name exists" in error_msg or "already exists" in error_msg.lower():
            return {
                "success": False,
                "label": None,
                "error": "A label with this name already exists",
            }
        return {"success": False, "label": None, "error": error_msg}


def delete_label(label_id: str) -> dict:
    """Delete a Gmail label."""
    if not label_id:
        return {"success": False, "error": "Label ID is required"}

    service, error = get_gmail_service()
    if error:
        return {"success": False, "error": error}

    try:
        quota.execute_with_backoff(
            service.users().labels().delete(userId="me", id=label_id),
            quota.COST["labels.delete"],
        )
        return {"success": True, "error": None}
    except Exception as e:
        error_msg = str(e)
        if "Not Found" in error_msg:
            return {"success": False, "error": "Label not found"}
        if "Cannot delete" in error_msg or "system label" in error_msg.lower():
            return {"success": False, "error": "Cannot delete system labels"}
        return {"success": False, "error": error_msg}


def _apply_label_operation_background(
    label_id: str,
    senders: list[str],
    *,
    add_label: bool,
    finding_message: str,
    applying_message: str,
    no_emails_message: str,
    progress_message_template: str,
    success_message_template: str,
    error_message_template: str,
    filters: Optional[dict] = None,
    excluded_message_ids: Optional[list[str]] = None,
) -> None:
    """Common helper for applying or removing labels from senders (background task).

    Args:
        label_id: The label ID to apply or remove
        senders: List of sender email addresses or domains
        add_label: If True, add the label; if False, remove it
        finding_message: Message to show while finding emails
        applying_message: Message to show while applying operation
        no_emails_message: Message when no emails found
        progress_message_template: Template for progress updates (use {count} and {total})
        success_message_template: Template for success message (use {count})
        error_message_template: Template for error message (use {count})
        filters: Filters that were active in the scan that surfaced these
            senders (see build_gmail_query) — keeps label operations scoped
            to the filtered subset the user reviewed, same root issue as #107.
        excluded_message_ids: Message IDs to leave untouched even though
            they match the sender+filters query — see delete.py's
            delete_emails_bulk_background for the query-minus-excluded
            reasoning (Phase 4c).
    """
    state.reset_label_operation()

    if not label_id or not label_id.strip():
        state.label_operation_status["done"] = True
        state.label_operation_status["error"] = "Label ID is required"
        return

    # Validate input
    if not senders or not isinstance(senders, list):
        state.label_operation_status["done"] = True
        state.label_operation_status["error"] = "No senders specified"
        return

    service, error = get_gmail_service()
    if error:
        state.label_operation_status["done"] = True
        state.label_operation_status["error"] = error
        return

    total_senders = len(senders)
    state.label_operation_status["total_senders"] = total_senders
    state.label_operation_status["message"] = finding_message

    # Phase 1: Collect all message IDs
    all_message_ids = []
    errors = []
    label_name = None

    # For remove operations, we need the label name for the query
    # Fetch it once before processing senders
    if not add_label:
        try:
            label_info = quota.execute_with_backoff(
                service.users().labels().get(userId="me", id=label_id),
                quota.COST["labels.get"],
                state.label_operation_status,
            )
            label_name = label_info.get("name", "")
            if not label_name:
                state.label_operation_status["done"] = True
                state.label_operation_status["error"] = "Could not get label name"
                return
        except Exception as e:
            state.label_operation_status["done"] = True
            state.label_operation_status["error"] = f"Failed to fetch label: {str(e)}"
            return
    else:
        # Not required for the query, but fetched best-effort so the
        # operation log can show a readable name instead of a bare ID.
        try:
            label_info = quota.execute_with_backoff(
                service.users().labels().get(userId="me", id=label_id),
                quota.COST["labels.get"],
                state.label_operation_status,
            )
            label_name = label_info.get("name", "")
        except Exception:
            label_name = None

    for i, sender in enumerate(senders):
        state.label_operation_status["current_sender"] = i + 1
        state.label_operation_status["progress"] = int((i / total_senders) * 40)
        state.label_operation_status["message"] = f"Finding emails from {sender}..."

        try:
            # Build query via build_gmail_query() so the operation stays scoped
            # to the filters active in the scan that surfaced this sender.
            # For remove, also constrain to messages that actually carry the
            # label being removed.
            query_filters = dict(filters or {})
            query_filters["sender"] = sender
            if not add_label:
                query_filters["label"] = label_name
            query = build_gmail_query(query_filters)

            results = quota.execute_with_backoff(
                service.users().messages().list(userId="me", q=query, maxResults=500),
                quota.COST["messages.list"],
                state.label_operation_status,
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
                    state.label_operation_status,
                )
                messages.extend(results.get("messages", []))

            all_message_ids.extend([msg["id"] for msg in messages])
        except Exception as e:
            errors.append(f"{sender}: {str(e)}")

    if excluded_message_ids:
        excluded = set(excluded_message_ids)
        all_message_ids = [m for m in all_message_ids if m not in excluded]

    if not all_message_ids:
        state.label_operation_status["progress"] = 100
        state.label_operation_status["done"] = True
        state.label_operation_status["message"] = no_emails_message
        return

    # Phase 2: Apply/remove label in batches
    total_emails = len(all_message_ids)
    state.label_operation_status["message"] = applying_message.format(
        count=total_emails
    )

    batch_size = 1000
    affected = 0
    affected_ids: list[str] = []

    # Build batch modify body
    if add_label:
        body_template = {"ids": None, "addLabelIds": [label_id]}
    else:
        body_template = {"ids": None, "removeLabelIds": [label_id]}

    try:
        for i in range(0, total_emails, batch_size):
            batch = all_message_ids[i : i + batch_size]
            body = {**body_template, "ids": batch}
            quota.execute_with_backoff(
                service.users().messages().batchModify(userId="me", body=body),
                quota.COST["messages.batchModify"],
                state.label_operation_status,
            )
            affected += len(batch)
            affected_ids.extend(batch)
            state.label_operation_status["affected_count"] = affected
            state.label_operation_status["progress"] = 40 + int(
                (affected / total_emails) * 60
            )
            state.label_operation_status["message"] = progress_message_template.format(
                count=affected, total=total_emails
            )
    except Exception as e:
        errors.append(f"Batch operation error: {str(e)}")

    if affected_ids:
        operation_log.append_entry(
            action_type="label_add" if add_label else "label_remove",
            message_ids=affected_ids,
            added_labels=[label_id] if add_label else [],
            removed_labels=[] if add_label else [label_id],
            summary={"senders": senders, "label_name": label_name},
            account_email=state.current_user.get("email"),
        )

    # Done
    state.label_operation_status["progress"] = 100
    state.label_operation_status["done"] = True
    state.label_operation_status["affected_count"] = affected

    if errors:
        state.label_operation_status["error"] = f"Some errors: {'; '.join(errors[:3])}"
        state.label_operation_status["message"] = error_message_template.format(
            count=affected
        )
    else:
        state.label_operation_status["message"] = success_message_template.format(
            count=affected
        )


def apply_label_to_senders_background(
    label_id: str,
    senders: list[str],
    filters: Optional[dict] = None,
    excluded_message_ids: Optional[list[str]] = None,
) -> None:
    """Apply a label to all emails from specified senders (background task)."""
    _apply_label_operation_background(
        label_id=label_id,
        senders=senders,
        add_label=True,
        finding_message="Finding emails to label...",
        applying_message="Applying label to {count} emails...",
        no_emails_message="No emails found to label",
        progress_message_template="Labeled {count}/{total} emails...",
        success_message_template="Successfully labeled {count} emails",
        error_message_template="Labeled {count} emails with some errors",
        filters=filters,
        excluded_message_ids=excluded_message_ids,
    )


def remove_label_from_senders_background(
    label_id: str,
    senders: list[str],
    filters: Optional[dict] = None,
    excluded_message_ids: Optional[list[str]] = None,
) -> None:
    """Remove a label from all emails from specified senders (background task)."""
    _apply_label_operation_background(
        label_id=label_id,
        senders=senders,
        add_label=False,
        finding_message="Finding emails to unlabel...",
        applying_message="Removing label from {count} emails...",
        no_emails_message="No emails found with this label",
        progress_message_template="Unlabeled {count}/{total} emails...",
        success_message_template="Successfully removed label from {count} emails",
        error_message_template="Unlabeled {count} emails with some errors",
        filters=filters,
        excluded_message_ids=excluded_message_ids,
    )


def get_label_operation_status() -> dict:
    """Get label operation status."""
    return state.label_operation_status.copy()
