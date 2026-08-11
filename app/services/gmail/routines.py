"""
Gmail Routine Execution (Phase 4b)
------------------------------------
Preview and run a saved Routine (app/services/routines.py's CRUD storage) -
a named preset of senders, a relative age threshold, and one or more
actions (delete/archive/mark_read/label) to apply to them.

A Routine's actions are combined into a single label diff (e.g. "Delete" +
"Label" becomes one addLabelIds=[TRASH, label_id] / removeLabelIds=[INBOX]
batchModify body) rather than running each selected action as its own pass
over the same messages - cheaper, and produces one operation-log entry per
run so the whole run undoes as one unit via Restore, tagged with the
Routine's name as its source (PRD: "Every Routine run writes to the Phase 2
operation log... this is what makes a run undoable").

Preview runs synchronously (messages.list only, no messages.get/batchModify)
since a Routine's sender list is small and curated, not an open-ended scan -
same reasoning as delete_emails_by_sender's single-sender delete running
inline rather than as a background task.
"""

from typing import Optional

from app.core import state
from app.services import accounts, operation_log, routines
from app.services.auth import get_gmail_service
from app.services.gmail import quota
from app.services.gmail.helpers import build_gmail_query


def _label_diff_for_actions(
    actions: list[str], label_id: Optional[str]
) -> tuple[list[str], list[str]]:
    """Combine a Routine's selected actions into one addLabelIds/removeLabelIds diff."""
    add: set[str] = set()
    remove: set[str] = set()
    if "delete" in actions:
        add.add("TRASH")
        remove.add("INBOX")
    if "archive" in actions:
        remove.add("INBOX")
    if "mark_read" in actions:
        remove.add("UNREAD")
    if "label" in actions and label_id:
        add.add(label_id)
    return sorted(add), sorted(remove)


def preview_routine(routine_id: str) -> Optional[dict]:
    """Compute per-sender + total match counts for a Routine, before running it.

    Returns None if the routine doesn't exist (or belongs to a different
    account). Raises on a Gmail auth failure - the API layer turns that
    into a 500, same as other action endpoints that call get_gmail_service()
    inline (see api_sign_in/api_unsubscribe).
    """
    account_email = accounts.get_active_account()
    routine = routines.get_routine(routine_id, account_email=account_email)
    if routine is None:
        return None

    service, error = get_gmail_service()
    if error:
        raise RuntimeError(error)

    per_sender = []
    total = 0
    for sender in routine["senders"]:
        query = build_gmail_query({"sender": sender, "older_than": routine["older_than"]})
        count = 0
        page_token = None
        while True:
            result = quota.execute_with_backoff(
                service.users()
                .messages()
                .list(userId="me", q=query, maxResults=500, pageToken=page_token),
                quota.COST["messages.list"],
                label="messages.list (routine preview)",
            )
            count += len(result.get("messages", []))
            page_token = result.get("nextPageToken")
            if not page_token:
                break
        per_sender.append({"sender": sender, "count": count})
        total += count

    return {
        "routine_id": routine["id"],
        "name": routine["name"],
        "total": total,
        "per_sender": per_sender,
        "actions": routine["actions"],
    }


def run_routine_background(routine_id: str) -> None:
    """Execute a Routine's combined action(s) against its configured senders.

    Two-phase, same shape as delete_emails_bulk_background: collect every
    matching message ID across all senders first (0-40% progress), then
    apply the combined label diff in large batches (40-100%) - the diff
    doesn't vary by sender, so every matched message can share one batch
    pass regardless of which sender it came from.
    """
    state.reset_routine_run()

    account_email = accounts.get_active_account()
    routine = routines.get_routine(routine_id, account_email=account_email)
    if routine is None:
        state.routine_run_status["done"] = True
        state.routine_run_status["error"] = "Routine not found"
        return

    senders = routine["senders"]
    total_senders = len(senders)
    state.routine_run_status["total_senders"] = total_senders
    state.routine_run_status["message"] = f"Running \"{routine['name']}\"..."

    service, error = get_gmail_service()
    if error:
        state.routine_run_status["done"] = True
        state.routine_run_status["error"] = error
        return

    add_label_ids, remove_label_ids = _label_diff_for_actions(
        routine["actions"], routine.get("label_id")
    )

    # Phase 1: collect all matching message IDs across every sender.
    all_message_ids: list[str] = []
    errors: list[str] = []
    for i, sender in enumerate(senders):
        state.routine_run_status["current_sender"] = i + 1
        state.routine_run_status["progress"] = int((i / total_senders) * 40)
        state.routine_run_status["message"] = f"Finding emails from {sender}..."

        try:
            query = build_gmail_query(
                {"sender": sender, "older_than": routine["older_than"]}
            )
            page_token = None
            while True:
                result = quota.execute_with_backoff(
                    service.users()
                    .messages()
                    .list(userId="me", q=query, maxResults=500, pageToken=page_token),
                    quota.COST["messages.list"],
                    state.routine_run_status,
                    label="messages.list (routine run)",
                )
                all_message_ids.extend(m["id"] for m in result.get("messages", []))
                page_token = result.get("nextPageToken")
                if not page_token:
                    break
        except Exception as e:
            errors.append(f"{sender}: {e}")

    # Phase 2: apply the combined diff in large batches.
    affected_ids: list[str] = []
    try:
        if all_message_ids:
            state.routine_run_status["message"] = (
                f"Applying actions to {len(all_message_ids)} emails..."
            )
            batch_size = 1000
            for i in range(0, len(all_message_ids), batch_size):
                chunk = all_message_ids[i : i + batch_size]
                body: dict = {"ids": chunk}
                if add_label_ids:
                    body["addLabelIds"] = add_label_ids
                if remove_label_ids:
                    body["removeLabelIds"] = remove_label_ids
                quota.execute_with_backoff(
                    service.users().messages().batchModify(userId="me", body=body),
                    quota.COST["messages.batchModify"],
                    state.routine_run_status,
                    label="messages.batchModify (routine run)",
                )
                affected_ids.extend(chunk)
                state.routine_run_status["affected_count"] = len(affected_ids)
                state.routine_run_status["progress"] = 40 + int(
                    (len(affected_ids) / len(all_message_ids)) * 60
                )
    except Exception as e:
        errors.append(f"Batch operation error: {e}")
    finally:
        # Log whatever actually succeeded, even if a later batch raised -
        # restoring must only ever cover messages truly modified. Tagged
        # with the Routine's name as `source` so Restore can attribute the
        # entry to this run (PRD: "tagged with the Routine's name").
        if affected_ids:
            operation_log.append_entry(
                action_type="routine",
                message_ids=affected_ids,
                added_labels=add_label_ids,
                removed_labels=remove_label_ids,
                summary={
                    "senders": senders,
                    "routine_name": routine["name"],
                    "actions": routine["actions"],
                },
                source=routine["name"],
                account_email=account_email,
            )
        routines.mark_routine_run(routine_id)

    state.routine_run_status["progress"] = 100
    state.routine_run_status["done"] = True
    state.routine_run_status["affected_count"] = len(affected_ids)

    if errors:
        state.routine_run_status["error"] = "; ".join(errors[:3])
        state.routine_run_status["message"] = (
            f"Routine finished with some errors ({len(affected_ids)} emails affected)"
        )
    elif not all_message_ids:
        state.routine_run_status["message"] = "No matching emails found"
    else:
        state.routine_run_status["message"] = (
            f"Routine \"{routine['name']}\" affected {len(affected_ids)} emails"
        )


def get_routine_run_status() -> dict:
    """Get the most recent routine-run status."""
    return state.routine_run_status.copy()
