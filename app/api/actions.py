"""
Actions API Routes
------------------
POST endpoints for triggering operations.
"""

import logging
from functools import partial
from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from app.core import state
from app.models import (
    DeleteScanRequest,
    ArchiveScanRequest,
    MarkReadScanRequest,
    MarkReadBulkRequest,
    UnsubscribeRequest,
    DeleteEmailsRequest,
    DeleteBulkRequest,
    DownloadEmailsRequest,
    CreateLabelRequest,
    ApplyLabelRequest,
    RemoveLabelRequest,
    ArchiveRequest,
    MarkImportantRequest,
)
from app.services import (
    get_gmail_service,
    sign_out,
    unsubscribe_single,
    scan_senders_for_markread,
    mark_emails_as_read_bulk_background,
    scan_senders_for_delete,
    delete_emails_by_sender,
    delete_emails_bulk_background,
    download_emails_background,
    create_label,
    delete_label,
    apply_label_to_senders_background,
    remove_label_from_senders_background,
    scan_senders_for_archive,
    archive_emails_background,
    mark_important_background,
)

router = APIRouter(prefix="/api", tags=["Actions"])
logger = logging.getLogger(__name__)


def _filters_dict(filters) -> dict | None:
    """Convert a FiltersModel (or None) to a plain dict for build_gmail_query."""
    return filters.model_dump(exclude_none=True) if filters else None


@router.post("/sign-in")
async def api_sign_in():
    """Trigger OAuth sign-in flow.

    Calls get_gmail_service() inline (not as a background task) so a
    genuine failure - e.g. a previous sign-in attempt still pending - can
    be surfaced to the caller immediately. The previous background-task
    version always returned {"status": "signing_in"} regardless of what
    actually happened server-side, silently swallowing that error. The
    OAuth browser flow itself still runs in get_gmail_service()'s own
    background thread and does not block this request.
    """
    try:
        _service, error = get_gmail_service()
    except Exception as e:
        logger.exception("Error starting sign-in")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start sign-in",
        ) from e

    if error:
        # "Sign-in started..." isn't a failure - the OAuth flow is now
        # running in the background and the frontend should poll for it.
        if error.startswith("Sign-in started"):
            return {"status": "signing_in"}
        return {"status": "error", "error": error}

    return {"status": "signed_in"}


@router.post("/sign-out")
async def api_sign_out():
    """Sign out and clear credentials."""
    try:
        return sign_out()
    except Exception as e:
        logger.exception("Error during sign-out")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to sign out",
        ) from e


@router.post("/unsubscribe")
async def api_unsubscribe(request: UnsubscribeRequest):
    """Unsubscribe from a single sender.

    Phase 3: called per-row from the merged Delete view's "Unsub" toggle -
    there's no separate Unsubscribe tab/scan anymore (see PROGRESS.md).
    """
    try:
        return unsubscribe_single(request.domain, request.link)
    except Exception as e:
        logger.exception("Error during unsubscribe")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to unsubscribe",
        ) from e


@router.post("/markread-scan")
async def api_markread_scan(
    request: MarkReadScanRequest, background_tasks: BackgroundTasks
):
    """Scan senders with unread mail for Mark-as-read's own sender-row list."""
    background_tasks.add_task(
        scan_senders_for_markread, request.limit, _filters_dict(request.filters)
    )
    return {"status": "started"}


@router.post("/mark-read-bulk")
async def api_mark_read_bulk(
    request: MarkReadBulkRequest, background_tasks: BackgroundTasks
):
    """Mark unread emails as read for selected senders (background task)."""
    background_tasks.add_task(
        mark_emails_as_read_bulk_background,
        request.senders,
        _filters_dict(request.filters),
        request.excluded_message_ids,
    )
    return {"status": "started"}


@router.post("/delete-scan")
async def api_delete_scan(
    request: DeleteScanRequest, background_tasks: BackgroundTasks
):
    """Scan senders for bulk delete (also surfaces unsubscribe status per
    sender, Phase 3 - see PROGRESS.md)."""
    background_tasks.add_task(
        scan_senders_for_delete, request.limit, _filters_dict(request.filters)
    )
    return {"status": "started"}


@router.post("/delete-emails")
async def api_delete_emails(request: DeleteEmailsRequest):
    """Delete emails from a specific sender."""
    if not request.sender or not request.sender.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sender email is required",
        )
    try:
        return delete_emails_by_sender(request.sender, state.delete_scan_filters)
    except Exception as e:
        logger.exception("Error deleting emails")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete emails",
        ) from e


@router.post("/delete-emails-bulk")
async def api_delete_emails_bulk(
    request: DeleteBulkRequest, background_tasks: BackgroundTasks
):
    """Delete emails from multiple senders (background task with progress)."""
    background_tasks.add_task(
        delete_emails_bulk_background,
        request.senders,
        state.delete_scan_filters,
        request.excluded_message_ids,
    )
    return {"status": "started"}


@router.post("/download-emails")
async def api_download_emails(
    request: DownloadEmailsRequest, background_tasks: BackgroundTasks
):
    """Start downloading email metadata for selected senders."""
    # Note: Empty list is allowed - service function will handle it gracefully
    background_tasks.add_task(download_emails_background, request.senders)
    return {"status": "started"}


# ----- Label Management Endpoints -----


@router.post("/labels")
async def api_create_label(request: CreateLabelRequest):
    """Create a new Gmail label."""
    try:
        return create_label(request.name)
    except Exception as e:
        logger.exception("Error creating label")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create label",
        ) from e


@router.delete("/labels/{label_id}")
async def api_delete_label(label_id: str):
    """Delete a Gmail label."""
    if not label_id or not label_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Label ID is required",
        )
    try:
        return delete_label(label_id)
    except Exception as e:
        logger.exception("Error deleting label")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete label",
        ) from e


@router.post("/apply-label")
async def api_apply_label(
    request: ApplyLabelRequest, background_tasks: BackgroundTasks
):
    """Apply a label to emails from selected senders.

    Phase 3: Label is now a per-row inline action on Delete/Mark-as-read/
    Archive alike (PRD Section 5), so the caller supplies whichever view's
    active filters surfaced the sender instead of always assuming Delete's
    (state.delete_scan_filters).
    """
    if not request.label_id or not request.label_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Label ID is required",
        )
    if not request.senders:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sender is required",
        )
    background_tasks.add_task(
        apply_label_to_senders_background,
        request.label_id,
        request.senders,
        _filters_dict(request.filters),
        request.excluded_message_ids,
    )
    return {"status": "started"}


@router.post("/remove-label")
async def api_remove_label(
    request: RemoveLabelRequest, background_tasks: BackgroundTasks
):
    """Remove a label from emails from selected senders (Phase 3: per-row,
    see api_apply_label)."""
    if not request.label_id or not request.label_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Label ID is required",
        )
    if not request.senders:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sender is required",
        )
    background_tasks.add_task(
        remove_label_from_senders_background,
        request.label_id,
        request.senders,
        _filters_dict(request.filters),
        request.excluded_message_ids,
    )
    return {"status": "started"}


@router.post("/archive-scan")
async def api_archive_scan(
    request: ArchiveScanRequest, background_tasks: BackgroundTasks
):
    """Scan senders for Archive's own sender-row list (Phase 3 - Archive
    previously had no scan of its own, see PROGRESS.md)."""
    background_tasks.add_task(
        scan_senders_for_archive, request.limit, _filters_dict(request.filters)
    )
    return {"status": "started"}


@router.post("/archive")
async def api_archive(request: ArchiveRequest, background_tasks: BackgroundTasks):
    """Archive emails from selected senders (remove from inbox)."""
    if not request.senders:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sender is required",
        )
    background_tasks.add_task(
        archive_emails_background,
        request.senders,
        _filters_dict(request.filters),
        request.excluded_message_ids,
    )
    return {"status": "started"}


@router.post("/mark-important")
async def api_mark_important(
    request: MarkImportantRequest, background_tasks: BackgroundTasks
):
    """Mark/unmark emails from selected senders as important (Phase 3:
    per-row inline action across Delete/Mark-as-read/Archive)."""
    if not request.senders:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one sender is required",
        )
    background_tasks.add_task(
        partial(
            mark_important_background,
            request.senders,
            important=request.important,
            filters=_filters_dict(request.filters),
        )
    )
    return {"status": "started"}
