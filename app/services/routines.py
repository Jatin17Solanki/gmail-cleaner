"""
Routines Storage (Phase 4b)
----------------------------
CRUD persistence for saved Routines: a named preset of sender addresses, a
relative age threshold, and one or more actions (delete/archive/mark_read/
label) to apply to them. Manual trigger only for this build - the
`schedule` field is stored but always None, reserved so a future cron-based
trigger can be added without restructuring existing data (PRD Section 6,
Phase 4b).

Persistence follows the same pattern as operation_log.py: a flat JSON file
(list of dicts) alongside token.json in the data volume, plain
open()/json.dump()/json.load(), defensive handling of a missing/empty/
corrupt file, failures logged rather than raised.

Routines are scoped to a single account (PRD: "not spanning multiple
accounts") via an `account_email` tag on each entry, same approach
operation_log.py uses for Phase 4a account scoping - a shared flat file
with a filter, not one file per account, since routines are rarely written
and there's no risk of cross-account replay the way there is for restore.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_routines_lock = threading.Lock()


def _routines_path() -> str:
    """Path to the routines file, alongside token.json (./data in Docker)."""
    data_dir = os.path.dirname(os.path.abspath(settings.token_file))
    return os.path.join(data_dir, "routines.json")


def _is_file_empty(path: str) -> bool:
    try:
        return os.path.getsize(path) == 0
    except OSError:
        return True


def _load_routines_unlocked() -> list[dict]:
    """Load raw routines from disk. Caller must hold `_routines_lock`."""
    path = _routines_path()
    if not os.path.exists(path) or _is_file_empty(path):
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, list):
            return []
        return data
    except (ValueError, OSError):
        logger.warning("Corrupt routines file at %s, treating as empty", path)
        return []


def _save_routines_unlocked(routines: list[dict]) -> None:
    """Persist routines to disk. Caller must hold `_routines_lock`."""
    path = _routines_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(routines, f)
    except OSError:
        logger.warning("Failed to persist routines to %s", path)


def list_routines(account_email: Optional[str] = None) -> list[dict]:
    """Return saved routines, most recently created first.

    Args:
        account_email: If given, only routines belonging to this account are
            returned (Phase 4b: Routines are single-account-scoped).
    """
    with _routines_lock:
        routines = _load_routines_unlocked()
    if account_email is not None:
        routines = [r for r in routines if r.get("account_email") == account_email]
    return sorted(routines, key=lambda r: r["created_at"], reverse=True)


def get_routine(routine_id: str, account_email: Optional[str] = None) -> Optional[dict]:
    """Read-only lookup of a single routine, or None if missing/wrong account."""
    for routine in list_routines(account_email=account_email):
        if routine["id"] == routine_id:
            return routine
    return None


def create_routine(
    name: str,
    senders: list[str],
    older_than: str,
    actions: list[str],
    account_email: str,
    label_id: Optional[str] = None,
    label_name: Optional[str] = None,
) -> dict:
    """Save a new Routine, scoped to `account_email`."""
    routine = {
        "id": uuid.uuid4().hex,
        "account_email": account_email,
        "name": name,
        "senders": senders,
        "older_than": older_than,
        "actions": actions,
        "label_id": label_id,
        "label_name": label_name,
        "schedule": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "last_run_at": None,
    }
    with _routines_lock:
        routines = _load_routines_unlocked()
        routines.append(routine)
        _save_routines_unlocked(routines)
    return routine


def delete_routine(routine_id: str, account_email: Optional[str] = None) -> Optional[dict]:
    """Remove and return a routine, or None if it wasn't found (or belongs
    to a different account)."""
    with _routines_lock:
        routines = _load_routines_unlocked()
        removed = None
        remaining = []
        for routine in routines:
            if (
                removed is None
                and routine["id"] == routine_id
                and (account_email is None or routine.get("account_email") == account_email)
            ):
                removed = routine
            else:
                remaining.append(routine)
        if removed is not None:
            _save_routines_unlocked(remaining)
    return removed


def mark_routine_run(routine_id: str) -> None:
    """Stamp a routine's `last_run_at` with the current time."""
    with _routines_lock:
        routines = _load_routines_unlocked()
        changed = False
        for routine in routines:
            if routine["id"] == routine_id:
                routine["last_run_at"] = datetime.now(timezone.utc).isoformat()
                changed = True
                break
        if changed:
            _save_routines_unlocked(routines)
