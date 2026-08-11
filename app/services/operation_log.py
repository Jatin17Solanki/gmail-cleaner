"""
Operation Log
-------------
Local, app-scoped log of destructive/label-changing actions (Phase 2:
Restore-from-Trash), so any batch action taken through this app can be
undone. Deliberately not a "restore everything in Gmail Trash" approach,
which would be imprecise and could resurrect messages the user trashed
manually and meant to delete.

Every entry records exactly the `ids`/`addLabelIds`/`removeLabelIds` triple
that was passed to Gmail's `messages().batchModify()` for that action.
Restoring an entry is always the same generic operation regardless of
action type: call `batchModify` again with `addLabelIds`/`removeLabelIds`
swapped (see app/services/gmail/restore.py).

Persistence follows the same pattern as app/core/security.py's
`auth.json` handling: a JSON file alongside `token.json` in the data
volume, plain open()/json.dump()/json.load(), defensive handling of a
missing/empty/corrupt file, and failures logged rather than raised so a
logging problem never blocks the Gmail action it's recording.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30

_log_lock = threading.Lock()


def _operation_log_path() -> str:
    """Path to the operation log file, alongside token.json (./data in Docker)."""
    data_dir = os.path.dirname(os.path.abspath(settings.token_file))
    return os.path.join(data_dir, "operations.json")


def _is_file_empty(path: str) -> bool:
    try:
        return os.path.getsize(path) == 0
    except OSError:
        return True


def _load_entries_unlocked() -> list[dict]:
    """Load raw entries from disk. Caller must hold `_log_lock`."""
    path = _operation_log_path()
    if not os.path.exists(path) or _is_file_empty(path):
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except (ValueError, OSError):
        logger.warning("Corrupt operation log at %s, treating as empty", path)
        return []


def _save_entries_unlocked(entries: list[dict]) -> None:
    """Persist entries to disk. Caller must hold `_log_lock`."""
    path = _operation_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(entries, f)
    except OSError:
        logger.warning("Failed to persist operation log to %s", path)


def _prune(entries: list[dict]) -> list[dict]:
    """Drop entries older than RETENTION_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    kept = []
    for entry in entries:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
        except (KeyError, ValueError):
            continue
        if ts >= cutoff:
            kept.append(entry)
    return kept


def append_entry(
    action_type: str,
    message_ids: list[str],
    added_labels: list[str],
    removed_labels: list[str],
    summary: Optional[dict] = None,
    source: str = "manual",
    account_email: Optional[str] = None,
) -> dict:
    """Record a completed action so it can be restored later.

    Args:
        action_type: e.g. "delete", "archive", "mark_read", "label_add",
            "label_remove".
        message_ids: IDs actually modified (only from successful batches).
        added_labels: Label IDs added by the action (removed on restore).
        removed_labels: Label IDs removed by the action (re-added on restore).
        summary: Small dict of display data (e.g. senders, label_name) so
            the API layer doesn't need to re-derive it.
        source: "manual" or, once Routines exist, a routine name.
        account_email: Which Gmail account this action ran against (Phase
            4a) - lets Restore scope itself to the active account so it
            never replays a batchModify against message IDs that belong to
            a different mailbox.
    """
    entry = {
        "id": uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action_type": action_type,
        "source": source,
        "message_ids": message_ids,
        "added_labels": added_labels,
        "removed_labels": removed_labels,
        "summary": summary or {},
        "account_email": account_email,
    }
    with _log_lock:
        entries = _prune(_load_entries_unlocked())
        entries.append(entry)
        _save_entries_unlocked(entries)
    return entry


def list_entries(account_email: Optional[str] = None) -> list[dict]:
    """Return non-expired entries, most recent first.

    Args:
        account_email: If given, only entries tagged with this account are
            returned. Entries with no account tag at all (pre-Phase-4a,
            never backfilled) are only included when account_email is None,
            since we can't be sure which account they belong to.
    """
    with _log_lock:
        entries = _prune(_load_entries_unlocked())
        _save_entries_unlocked(entries)
    if account_email is not None:
        entries = [e for e in entries if e.get("account_email") == account_email]
    return sorted(entries, key=lambda e: e["timestamp"], reverse=True)


def find_entry(entry_id: str, account_email: Optional[str] = None) -> Optional[dict]:
    """Read-only lookup of a single entry, or None if missing/expired/wrong account."""
    for entry in list_entries(account_email=account_email):
        if entry["id"] == entry_id:
            return entry
    return None


def backfill_account_email(email: str) -> None:
    """Tag pre-Phase-4a entries (no account_email) with `email`.

    Called once, alongside legacy token migration - at the time those
    entries were written this app only ever supported one account, so
    they're unambiguously this one.
    """
    with _log_lock:
        entries = _load_entries_unlocked()
        changed = False
        for entry in entries:
            if not entry.get("account_email"):
                entry["account_email"] = email
                changed = True
        if changed:
            _save_entries_unlocked(entries)


def remove_entry(entry_id: str) -> Optional[dict]:
    """Remove and return an entry, or None if it wasn't found."""
    with _log_lock:
        entries = _prune(_load_entries_unlocked())
        removed = None
        remaining = []
        for entry in entries:
            if entry["id"] == entry_id and removed is None:
                removed = entry
            else:
                remaining.append(entry)
        if removed is not None:
            _save_entries_unlocked(remaining)
        else:
            _save_entries_unlocked(entries)
    return removed
