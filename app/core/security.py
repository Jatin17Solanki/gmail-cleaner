"""
App Login Gate
---------------
Shared-password session authentication protecting the entire app (Phase 1.3,
resolves #109/#108/#111). Independent of Gmail OAuth (app/services/auth.py) —
this gate protects the UI itself, before any Gmail account is ever involved.

Sessions are kept in memory only (no database, per Section 7): a session
token that isn't in `_active_sessions` is not valid, full stop. This means a
container restart invalidates all sessions, which is an accepted trade-off
for a single-user local tool with no self-service password reset.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

SESSION_COOKIE_NAME = "session"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days

# token -> created_at (epoch seconds). In-memory only, see module docstring.
_active_sessions: dict[str, float] = {}


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _auth_file_path() -> str:
    """Path to the persisted password hash, alongside token.json (./data in Docker)."""
    data_dir = os.path.dirname(os.path.abspath(settings.token_file))
    return os.path.join(data_dir, "auth.json")


def ensure_password_hash_persisted() -> None:
    """Persist a hash of the configured password to the data volume (Section 7).

    Not used for verification (that always re-checks against the live env
    var, which is the source of truth) — this exists so the data volume's
    inventory matches the documented storage architecture. Safe no-op if no
    password is configured, and failures here must never block startup.
    """
    if not settings.app_password:
        return
    path = _auth_file_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump({"password_hash": _hash_password(settings.app_password)}, f)
    except OSError:
        logger.warning("Failed to persist auth password hash to %s", path)


def is_auth_enabled() -> bool:
    """Whether the login gate is active (only when APP_PASSWORD is set)."""
    return bool(settings.app_password)


def verify_password(password: str) -> bool:
    """Constant-time check of a submitted password against the configured one."""
    if not settings.app_password:
        return False
    expected = _hash_password(settings.app_password)
    actual = _hash_password(password or "")
    return hmac.compare_digest(expected, actual)


def create_session() -> str:
    """Create a new session and return its token."""
    token = secrets.token_urlsafe(32)
    _active_sessions[token] = time.time()
    return token


def is_valid_session(token: Optional[str]) -> bool:
    """Whether a session token is present and not expired."""
    if not token:
        return False
    created = _active_sessions.get(token)
    if created is None:
        return False
    if time.time() - created > SESSION_TTL_SECONDS:
        _active_sessions.pop(token, None)
        return False
    return True


def destroy_session(token: Optional[str]) -> None:
    """Invalidate a session token, if present. No-op for unknown/missing tokens."""
    if token:
        _active_sessions.pop(token, None)
