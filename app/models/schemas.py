"""
Pydantic Models - Request/Response Schemas
------------------------------------------
Data validation and serialization.
"""

from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator
import re


# ----- Filter Model -----


class FiltersModel(BaseModel):
    """Gmail filter options with validation."""

    older_than: Optional[str] = Field(
        default=None,
        description="Filter emails older than (e.g., 7d, 30d, 90d, 180d, 365d)",
    )
    after_date: Optional[str] = Field(
        default=None, description="Filter emails after this date (format: YYYY/MM/DD)"
    )
    before_date: Optional[str] = Field(
        default=None, description="Filter emails before this date (format: YYYY/MM/DD)"
    )
    larger_than: Optional[str] = Field(
        default=None, description="Filter emails larger than (e.g., 1M, 5M, 10M)"
    )
    category: Optional[str] = Field(default=None, description="Gmail category filter")
    sender: Optional[str] = Field(
        default=None,
        description="Filter emails from specific sender (email address or domain)",
    )
    label: Optional[str] = Field(default=None, description="Gmail label filter")
    unread_only: Optional[bool] = Field(
        default=None, description="Restrict to unread mail (#99)"
    )
    has_attachment: Optional[bool] = Field(
        default=None, description="Restrict to mail with attachments (#99)"
    )

    @field_validator("older_than")
    @classmethod
    def validate_older_than(cls, v) -> Optional[str]:
        if v is None or v == "":
            return None
        if not re.match(r"^\d+d$", v):
            raise ValueError('older_than must be in format like "7d", "30d", "365d"')
        return v

    @field_validator("after_date")
    @classmethod
    def validate_after_date(cls, v) -> Optional[str]:
        if v is None or v == "":
            return None
        if not re.match(r"^\d{4}/\d{2}/\d{2}$", v):
            raise ValueError('after_date must be in format like "2025/01/15"')
        return v

    @field_validator("before_date")
    @classmethod
    def validate_before_date(cls, v) -> Optional[str]:
        if v is None or v == "":
            return None
        if not re.match(r"^\d{4}/\d{2}/\d{2}$", v):
            raise ValueError('before_date must be in format like "2025/01/15"')
        return v

    @field_validator("larger_than")
    @classmethod
    def validate_larger_than(cls, v) -> Optional[str]:
        if v is None or v == "":
            return None
        if not re.match(r"^\d+[KMG]$", v, re.IGNORECASE):
            raise ValueError('larger_than must be in format like "1M", "5M", "10M"')
        return v

    @field_validator("category")
    @classmethod
    def validate_category(cls, v) -> Optional[str]:
        if v is None or v == "":
            return None
        allowed = ["primary", "social", "promotions", "updates", "forums"]
        if v.lower() not in allowed:
            raise ValueError(f"category must be one of: {allowed}")
        return v.lower()

    @field_validator("sender")
    @classmethod
    def validate_sender(cls, v) -> Optional[str]:
        if v is None or v == "":
            return None
        # Allow email addresses or domain names
        sender = v.strip()
        if not sender:
            return None
        # Basic validation: must contain @ or be a domain-like string
        if "@" not in sender and "." not in sender:
            raise ValueError("sender must be a valid email address or domain")
        return sender


# ----- Request Models -----


class LoginRequest(BaseModel):
    """Request to authenticate against the app's shared password."""

    password: str = Field(default="", description="Shared app password")


class DeleteScanRequest(BaseModel):
    """Request to scan senders for deletion."""

    limit: int = Field(default=500, ge=1, le=10000, description="Max emails to scan")
    filters: Optional[FiltersModel] = Field(
        default=None, description="Gmail filter options"
    )


class ArchiveScanRequest(BaseModel):
    """Request to scan senders for archiving."""

    limit: int = Field(default=500, ge=1, le=10000, description="Max emails to scan")
    filters: Optional[FiltersModel] = Field(
        default=None, description="Gmail filter options"
    )


class MarkReadScanRequest(BaseModel):
    """Request to scan senders with unread mail."""

    limit: int = Field(default=500, ge=1, le=10000, description="Max emails to scan")
    filters: Optional[FiltersModel] = Field(
        default=None, description="Gmail filter options"
    )


class MarkReadBulkRequest(BaseModel):
    """Request to mark unread emails as read for selected senders."""

    senders: list[str] = Field(default=[], description="List of sender addresses")
    filters: Optional[FiltersModel] = Field(
        default=None, description="Gmail filter options"
    )
    excluded_message_ids: list[str] = Field(
        default=[],
        description="Message IDs to leave untouched, from unchecked per-message "
        "checkboxes in an expanded sender row (Phase 4c)",
    )


class UnsubscribeRequest(BaseModel):
    """Request to unsubscribe from a sender."""

    domain: str = Field(default="", description="Sender domain")
    link: str = Field(default="", description="Unsubscribe link URL")


class DeleteEmailsRequest(BaseModel):
    """Request to delete emails from a sender."""

    sender: str = Field(default="", description="Sender email address")


class DeleteBulkRequest(BaseModel):
    """Request to delete emails from multiple senders."""

    senders: list[str] = Field(default=[], description="List of sender addresses")
    excluded_message_ids: list[str] = Field(
        default=[],
        description="Message IDs to leave untouched, from unchecked per-message "
        "checkboxes in an expanded sender row (Phase 4c)",
    )


class DownloadEmailsRequest(BaseModel):
    """Request to download emails from selected senders."""

    senders: list[str] = Field(default=[], description="List of sender addresses")


class CreateLabelRequest(BaseModel):
    """Request to create a new Gmail label."""

    name: str = Field(..., min_length=1, max_length=100, description="Label name")


class ApplyLabelRequest(BaseModel):
    """Request to apply a label to emails from selected senders."""

    label_id: str = Field(..., description="Gmail label ID to apply")
    senders: list[str] = Field(default=[], description="List of sender addresses")
    filters: Optional[FiltersModel] = Field(
        default=None, description="Gmail filter options"
    )
    excluded_message_ids: list[str] = Field(
        default=[],
        description="Message IDs to leave untouched, from unchecked per-message "
        "checkboxes in an expanded sender row (Phase 4c)",
    )


class RemoveLabelRequest(BaseModel):
    """Request to remove a label from selected senders."""

    label_id: str = Field(..., description="Gmail label ID to remove")
    senders: list[str] = Field(default=[], description="List of sender addresses")
    filters: Optional[FiltersModel] = Field(
        default=None, description="Gmail filter options"
    )
    excluded_message_ids: list[str] = Field(
        default=[],
        description="Message IDs to leave untouched, from unchecked per-message "
        "checkboxes in an expanded sender row (Phase 4c)",
    )


class ArchiveRequest(BaseModel):
    """Request to archive emails from selected senders."""

    senders: list[str] = Field(default=[], description="List of sender addresses")
    filters: Optional[FiltersModel] = Field(
        default=None, description="Gmail filter options"
    )
    excluded_message_ids: list[str] = Field(
        default=[],
        description="Message IDs to leave untouched, from unchecked per-message "
        "checkboxes in an expanded sender row (Phase 4c)",
    )


class MarkImportantRequest(BaseModel):
    """Request to mark/unmark emails as important."""

    senders: list[str] = Field(default=[], description="List of sender addresses")
    important: bool = Field(
        default=True, description="True to mark important, False to unmark"
    )
    filters: Optional[FiltersModel] = Field(
        default=None, description="Gmail filter options"
    )


# ----- Routines (Phase 4b) -----

# "not delete-only" per PRD Section 6, Phase 4b — a Routine can combine any
# subset of these into one action.
ROUTINE_ACTIONS = {"delete", "archive", "mark_read", "label"}


class CreateRoutineRequest(BaseModel):
    """Request to create a saved Routine (scoped to the active account)."""

    name: str = Field(..., min_length=1, max_length=100, description="Routine name")
    senders: list[str] = Field(
        ..., min_length=1, description="Sender email addresses or domains"
    )
    older_than: str = Field(
        ..., description='Relative age threshold, e.g. "7d", "30d"'
    )
    actions: list[str] = Field(
        ..., min_length=1, description="One or more of: delete, archive, mark_read, label"
    )
    label_id: Optional[str] = Field(
        default=None, description="Gmail label ID — required when actions includes 'label'"
    )

    @field_validator("senders")
    @classmethod
    def validate_senders(cls, v: list[str]) -> list[str]:
        cleaned = [s.strip() for s in v if s and s.strip()]
        if not cleaned:
            raise ValueError("At least one sender is required")
        return cleaned

    @field_validator("older_than")
    @classmethod
    def validate_older_than(cls, v: str) -> str:
        if not re.match(r"^\d+d$", v):
            raise ValueError('older_than must be in format like "7d", "30d", "365d"')
        return v

    @field_validator("actions")
    @classmethod
    def validate_actions(cls, v: list[str]) -> list[str]:
        invalid = set(v) - ROUTINE_ACTIONS
        if invalid:
            raise ValueError(f"Invalid action(s): {sorted(invalid)}. Must be one of: {sorted(ROUTINE_ACTIONS)}")
        return v

    @model_validator(mode="after")
    def validate_label_id_present_when_labeling(self) -> "CreateRoutineRequest":
        if "label" in self.actions and not (self.label_id and self.label_id.strip()):
            raise ValueError("label_id is required when actions includes 'label'")
        return self


class RoutinePreviewSenderCount(BaseModel):
    """Per-sender match count shown in the Routine confirm step."""

    sender: str
    count: int


class RoutinePreviewResponse(BaseModel):
    """Preview of what a Routine run will match, shown before executing."""

    routine_id: str
    name: str
    total: int
    per_sender: list[RoutinePreviewSenderCount]
    actions: list[str]


class RoutineInfo(BaseModel):
    """A saved Routine (Phase 4b), scoped to a single account."""

    id: str
    name: str
    senders: list[str]
    older_than: str
    actions: list[str]
    label_id: Optional[str] = None
    label_name: Optional[str] = None
    schedule: Optional[str] = None
    created_at: str
    last_run_at: Optional[str] = None


# ----- Response Models -----


class StatusResponse(BaseModel):
    """Generic status response."""

    status: str


class AuthStatusResponse(BaseModel):
    """Authentication status response."""

    email: Optional[str] = None
    logged_in: bool = False


class ScanStatusResponse(BaseModel):
    """Scan progress status response."""

    progress: int = 0
    message: str = "Ready"
    done: bool = False
    error: Optional[str] = None


class UnsubscribeResponse(BaseModel):
    """Unsubscribe action response."""

    success: bool
    message: str
    domain: Optional[str] = None


class DeleteResponse(BaseModel):
    """Delete action response."""

    success: bool
    deleted: int = 0
    message: Optional[str] = None


class OperationLogEntry(BaseModel):
    """A restorable entry from the operation log (Phase 2)."""

    id: str
    action_type: str
    timestamp: str
    source: str
    message_count: int
    senders: list[str] = []
    label_name: Optional[str] = None


class RestoreResponse(BaseModel):
    """Restore action response."""

    success: bool
    restored: int = 0
    message: Optional[str] = None


class AccountInfo(BaseModel):
    """A Gmail account authorized against this instance (Phase 4a)."""

    email: str
    active: bool


class SwitchAccountRequest(BaseModel):
    """Body for POST /api/accounts/switch."""

    email: str


class SwitchAccountResponse(BaseModel):
    """Switch-account action response."""

    success: bool
    message: Optional[str] = None
