"""
Application Configuration
-------------------------
Central configuration and settings for the application.
"""

import logging
import os
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Docker's WORKDIR is /app (see Dockerfile), so this relative default
# resolves to /app/data/token.json inside a container - exactly the path
# docker-compose.yml's data/ bind mount already covers, with no runtime
# detection needed. Locally, it resolves under the cwd, so data/token.json
# sits in a data/ subfolder next to credentials.json at the repo root. Both
# modes land on the same relative layout automatically.
DEFAULT_TOKEN_FILE = "data/token.json"

# Pre-unification local (non-Docker) installs may have accumulated data
# files at this bare, repo-root-relative location instead of under data/ -
# Docker installs never had this problem, since the old /app/data
# auto-detection already put their files exactly where DEFAULT_TOKEN_FILE
# now points too. See migrate_legacy_data_layout() below.
LEGACY_TOKEN_FILE = "token.json"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Server
    app_name: str = "Gmail Cleaner"
    app_version: str = "1.0.0"
    debug: bool = False
    port: int = 8766
    oauth_port: int = 8767
    oauth_external_port: int | None = Field(
        default=None,
        description="External port for OAuth redirect URI (when different from oauth_port, e.g., Docker port mapping)",
    )

    # Auth
    web_auth: bool = Field(
        default=False,
        description="Enable web-based authentication mode",
    )
    oauth_host: str = Field(
        default="localhost",
        description="Custom host for OAuth redirect (e.g., your domain or IP)",
    )

    # App-level login gate (Phase 1.3, resolves #109/#108/#111)
    app_password: str | None = Field(
        default=None,
        description=(
            "Shared password gating the entire app (env: APP_PASSWORD). "
            "If unset, the login gate is disabled. Resetting the password "
            "means changing this env var and restarting the container — "
            "there is no self-service reset flow."
        ),
    )

    # Gmail API quota tracing (Phase 4a2 debugging aid). Off by default —
    # PRD Section 7 wants quota tracking silent by default, this is purely
    # for diagnosing scan pacing when investigating a specific issue.
    quota_trace_logging: bool = Field(
        default=False,
        description=(
            "Log a timestamped line for every Gmail API call the quota "
            "tracker charges (env: QUOTA_TRACE_LOGGING) — lets you see "
            "exactly when each call fired and the cumulative usage at that "
            "moment, instead of inferring wait cycles from the UI."
        ),
    )

    @field_validator("web_auth", "quota_trace_logging", mode="before")
    @classmethod
    def validate_web_auth(cls, v) -> bool:
        """Convert string environment variable to boolean (case-insensitive)."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            normalized = v.lower().strip()
            return normalized in ("true", "1", "yes", "on")
        return bool(v)

    credentials_file: str = "credentials.json"
    token_file: str = DEFAULT_TOKEN_FILE

    # Gmail API
    scopes: list[str] = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Global settings instance
settings = Settings()

_LEGACY_DATA_FILENAMES = (
    "token.json",
    "accounts.json",
    "operations.json",
    "auth.json",
    "routines.json",
)


def migrate_legacy_data_layout() -> None:
    """One-time move of pre-unification local data files into data/.

    No-op for Docker installs (their files were already under data/ via the
    old /app/data auto-detection this replaced) and for any install that's
    already been migrated or never had legacy files to begin with. Not
    called automatically at import time - filesystem-mutating side effects
    at import would run during test collection too, which is unsafe when a
    developer's actual working directory has real legacy files. Call once
    from the real app entrypoint (main.py) instead.

    Deliberately conservative: never overwrites an existing file at the new
    location, since accounts.py/operation_log.py/security.py/routines.py
    all key off token_file's directory together - a real install could have
    diverged data at both locations (e.g. local-Python and Docker runs
    signed into different accounts before this unification existed). Logs a
    warning instead of guessing which copy should win.
    """
    legacy_dir = os.path.dirname(os.path.abspath(LEGACY_TOKEN_FILE)) or "."
    new_dir = os.path.dirname(os.path.abspath(settings.token_file))
    if legacy_dir == new_dir:
        return

    for name in _LEGACY_DATA_FILENAMES:
        old_path = os.path.join(legacy_dir, name)
        new_path = os.path.join(new_dir, name)
        if not os.path.exists(old_path):
            continue
        if os.path.exists(new_path):
            logger.warning(
                "Found legacy data file %s but %s already exists - leaving "
                "both in place. Reconcile manually if the legacy copy is "
                "the one you want.",
                old_path,
                new_path,
            )
            continue
        os.makedirs(new_dir, exist_ok=True)
        os.replace(old_path, new_path)
        logger.info("Migrated legacy data file %s -> %s", old_path, new_path)

    old_tokens_dir = os.path.join(legacy_dir, "tokens")
    new_tokens_dir = os.path.join(new_dir, "tokens")
    if os.path.isdir(old_tokens_dir):
        if os.path.exists(new_tokens_dir):
            logger.warning(
                "Found legacy tokens directory %s but %s already exists - "
                "leaving both in place.",
                old_tokens_dir,
                new_tokens_dir,
            )
        else:
            os.makedirs(new_dir, exist_ok=True)
            os.replace(old_tokens_dir, new_tokens_dir)
            logger.info(
                "Migrated legacy tokens directory %s -> %s",
                old_tokens_dir,
                new_tokens_dir,
            )
