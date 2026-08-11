"""
Gmail Restore Operations
------------------------
Reverses a logged operation-log entry (Phase 2: Restore-from-Trash). Every
entry records the exact `ids`/`addLabelIds`/`removeLabelIds` triple used by
the original `batchModify` call, so restoring is always the same generic
operation regardless of what kind of action created the entry: call
`batchModify` again with `addLabelIds`/`removeLabelIds` swapped.
"""

from app.services import operation_log
from app.services.auth import get_gmail_service


def restore_operation(entry_id: str) -> dict:
    """Reverse a logged operation, restoring its messages to their prior state.

    Args:
        entry_id: The operation log entry's id.

    Returns:
        {"success": bool, "restored": int, "message": str}
    """
    entry = operation_log.find_entry(entry_id)
    if entry is None:
        return {"success": False, "restored": 0, "message": "Entry not found"}

    service, error = get_gmail_service()
    if error:
        return {"success": False, "restored": 0, "message": error}

    message_ids = entry["message_ids"]
    body_template: dict = {}
    if entry["removed_labels"]:
        body_template["addLabelIds"] = entry["removed_labels"]
    if entry["added_labels"]:
        body_template["removeLabelIds"] = entry["added_labels"]

    batch_size = 1000
    restored = 0

    try:
        for i in range(0, len(message_ids), batch_size):
            batch = message_ids[i : i + batch_size]
            body = {**body_template, "ids": batch}
            service.users().messages().batchModify(userId="me", body=body).execute()
            restored += len(batch)
    except Exception as e:
        return {
            "success": False,
            "restored": restored,
            "message": f"Restore failed after {restored} messages: {e}",
        }

    operation_log.remove_entry(entry_id)
    return {
        "success": True,
        "restored": restored,
        "message": f"Restored {restored} messages",
    }
