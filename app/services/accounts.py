"""
Multi-Account Storage (Phase 4a)
---------------------------------
Tracks which Gmail accounts have been authorized against this instance and
which one is currently active. Each account's OAuth token lives in its own
file under `./data/tokens/<email>.json` (sibling of the legacy single
`token.json`), keyed by the account's own email address so multiple
accounts' credentials never collide.

A small `accounts.json` index (same directory as `token.json`) is the
source of truth for the registered account list and the active pointer -
deliberately not derived from listing `tokens/` on disk, so a sanitized
filename can never drift from the real email address it represents.

Migration: instances that predate this phase have a single token at
`settings.token_file` with no email attached to it. `migrate_legacy_token()`
moves that file into the new per-account layout once the caller has
learned the account's email (via a Gmail profile call, same as the
existing sign-in/status flows already make) - see app/services/auth.py.
"""

import json
import logging
import os
import re
import threading
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

_index_lock = threading.Lock()

# Gmail addresses only ever contain these characters, but sanitize
# defensively anyway rather than trust API response content in a filename.
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9@._+-]")


def _data_dir() -> str:
    return os.path.dirname(os.path.abspath(settings.token_file))


def _tokens_dir() -> str:
    return os.path.join(_data_dir(), "tokens")


def _index_path() -> str:
    return os.path.join(_data_dir(), "accounts.json")


def _safe_filename(email: str) -> str:
    return _UNSAFE_FILENAME_CHARS.sub("_", email) + ".json"


def token_path_for(email: str) -> str:
    """Path an account's token is stored at (directory not guaranteed to exist)."""
    return os.path.join(_tokens_dir(), _safe_filename(email))


def _load_index_unlocked() -> dict:
    path = _index_path()
    if not os.path.exists(path):
        return {"active": None, "accounts": []}
    try:
        with open(path, "r") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"active": None, "accounts": []}
        return {
            "active": data.get("active"),
            "accounts": [e for e in data.get("accounts", []) if isinstance(e, str)],
        }
    except (ValueError, OSError):
        logger.warning("Corrupt accounts index at %s, treating as empty", path)
        return {"active": None, "accounts": []}


def _save_index_unlocked(index: dict) -> None:
    path = _index_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(index, f)
    except OSError:
        logger.warning("Failed to persist accounts index to %s", path)


def list_accounts() -> list[str]:
    """Registered account emails, in the order they were added."""
    with _index_lock:
        return _load_index_unlocked()["accounts"]


def get_active_account() -> Optional[str]:
    """The currently active account's email, or None if none is set yet."""
    with _index_lock:
        return _load_index_unlocked()["active"]


def set_active_account(email: str) -> bool:
    """Mark `email` as active. Returns False if it isn't a registered account."""
    with _index_lock:
        index = _load_index_unlocked()
        if email not in index["accounts"]:
            return False
        index["active"] = email
        _save_index_unlocked(index)
        return True


def register_account(email: str) -> None:
    """Add `email` to the registered account list if not already present."""
    with _index_lock:
        index = _load_index_unlocked()
        if email not in index["accounts"]:
            index["accounts"].append(email)
            _save_index_unlocked(index)


def remove_account(email: str) -> None:
    """Delete an account's token file and drop it from the index.

    If it was the active account, a remaining account (if any) becomes
    active; otherwise the active pointer clears.
    """
    token_path = token_path_for(email)
    if os.path.exists(token_path):
        try:
            os.remove(token_path)
        except OSError:
            logger.warning("Failed to remove token file for %s", email)

    with _index_lock:
        index = _load_index_unlocked()
        if email in index["accounts"]:
            index["accounts"].remove(email)
        if index["active"] == email:
            index["active"] = index["accounts"][0] if index["accounts"] else None
        _save_index_unlocked(index)


def resolve_active_token_path() -> Optional[str]:
    """Token file path for the active account.

    Falls back to the legacy pre-4a single `token.json` if no account has
    been registered yet (fresh migration hasn't happened, or the app has
    never learned an account's email) - the file, if present, is still a
    perfectly valid token, just not yet keyed by email.
    """
    active = get_active_account()
    if active:
        return token_path_for(active)
    if os.path.exists(settings.token_file):
        return settings.token_file
    return None


def migrate_legacy_token(email: str) -> None:
    """Move the legacy single-account token into per-account storage.

    No-op if there's nothing at the legacy path, or an account is already
    registered (migration already happened / this instance was always
    multi-account). Safe to call on every successful profile fetch.
    """
    if get_active_account() is not None or list_accounts():
        register_account(email)
        return

    if not os.path.exists(settings.token_file):
        register_account(email)
        set_active_account(email)
        return

    new_path = token_path_for(email)
    try:
        os.makedirs(_tokens_dir(), exist_ok=True)
        os.replace(settings.token_file, new_path)
    except OSError:
        logger.warning("Failed to migrate legacy token for %s", email)
        return

    register_account(email)
    set_active_account(email)
