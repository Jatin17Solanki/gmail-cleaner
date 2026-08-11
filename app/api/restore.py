"""
Restore API Routes
-------------------
Endpoints for the operation log / Restore-from-Trash screen (Phase 2).
"""

import logging

from fastapi import APIRouter

from app.models import OperationLogEntry, RestoreResponse
from app.services import operation_log, restore_operation

router = APIRouter(prefix="/api", tags=["Restore"])
logger = logging.getLogger(__name__)


def _to_entry_model(entry: dict) -> OperationLogEntry:
    summary = entry.get("summary") or {}
    return OperationLogEntry(
        id=entry["id"],
        action_type=entry["action_type"],
        timestamp=entry["timestamp"],
        source=entry["source"],
        message_count=len(entry.get("message_ids", [])),
        senders=summary.get("senders", []),
        label_name=summary.get("label_name"),
    )


@router.get("/restore")
async def api_list_restorable() -> list[OperationLogEntry]:
    """List restorable operation-log entries from the last 30 days."""
    entries = operation_log.list_entries()
    return [_to_entry_model(entry) for entry in entries]


@router.post("/restore/{entry_id}")
async def api_restore(entry_id: str) -> RestoreResponse:
    """Restore a logged batch of messages to their prior state."""
    result = restore_operation(entry_id)
    return RestoreResponse(**result)
