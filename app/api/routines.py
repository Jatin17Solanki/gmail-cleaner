"""
Routines API Routes (Phase 4b)
--------------------------------
CRUD + preview/run endpoints for saved Routines. Every endpoint is scoped
to the active account (accounts.get_active_account()) - Routines don't
span multiple accounts (PRD Section 6, Phase 4b).
"""

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.models import CreateRoutineRequest, RoutineInfo, RoutinePreviewResponse
from app.services import accounts, get_labels, routines
from app.services.gmail import (
    get_routine_run_status,
    preview_routine,
    run_routine_background,
)

router = APIRouter(prefix="/api/routines", tags=["Routines"])
logger = logging.getLogger(__name__)


def _resolve_label_name(label_id: str) -> str | None:
    """Best-effort label name lookup for display - a missing/failed lookup
    just means RoutineInfo.label_name is None, not a hard failure."""
    result = get_labels()
    if not result.get("success"):
        return None
    for label in result.get("system_labels", []) + result.get("user_labels", []):
        if label.get("id") == label_id:
            return label.get("name")
    return None


def _to_info(routine: dict) -> RoutineInfo:
    return RoutineInfo(
        id=routine["id"],
        name=routine["name"],
        senders=routine["senders"],
        older_than=routine["older_than"],
        actions=routine["actions"],
        label_id=routine.get("label_id"),
        label_name=routine.get("label_name"),
        schedule=routine.get("schedule"),
        created_at=routine["created_at"],
        last_run_at=routine.get("last_run_at"),
    )


@router.get("")
async def api_list_routines() -> list[RoutineInfo]:
    """List Routines saved for the active account."""
    saved = routines.list_routines(account_email=accounts.get_active_account())
    return [_to_info(r) for r in saved]


@router.post("")
async def api_create_routine(request: CreateRoutineRequest) -> RoutineInfo:
    """Save a new Routine, scoped to the active account."""
    account_email = accounts.get_active_account()
    if not account_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No active account - sign in before creating a Routine",
        )

    label_name = _resolve_label_name(request.label_id) if request.label_id else None

    routine = routines.create_routine(
        name=request.name,
        senders=request.senders,
        older_than=request.older_than,
        actions=request.actions,
        account_email=account_email,
        label_id=request.label_id,
        label_name=label_name,
    )
    return _to_info(routine)


@router.delete("/{routine_id}")
async def api_delete_routine(routine_id: str):
    """Delete a saved Routine."""
    removed = routines.delete_routine(
        routine_id, account_email=accounts.get_active_account()
    )
    if removed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Routine not found"
        )
    return {"success": True, "message": "Routine deleted"}


@router.post("/{routine_id}/preview")
async def api_preview_routine(routine_id: str) -> RoutinePreviewResponse:
    """Preview what a Routine run will match, before executing it - the
    required confirm step (PRD: "never executes silently on click")."""
    try:
        preview = preview_routine(routine_id)
    except Exception as e:
        logger.exception("Error previewing routine")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to preview routine",
        ) from e

    if preview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Routine not found"
        )
    return RoutinePreviewResponse(**preview)


@router.post("/{routine_id}/run")
async def api_run_routine(routine_id: str, background_tasks: BackgroundTasks):
    """Run a Routine's combined action(s) against its configured senders
    (background task with progress - see /api/routines/run-status)."""
    if routines.get_routine(routine_id, account_email=accounts.get_active_account()) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Routine not found"
        )
    background_tasks.add_task(run_routine_background, routine_id)
    return {"status": "started"}


@router.get("/run-status")
async def api_routine_run_status():
    """Get the most recent Routine run's status."""
    try:
        return get_routine_run_status()
    except Exception as e:
        logger.exception("Error getting routine run status")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to get routine run status",
        ) from e
