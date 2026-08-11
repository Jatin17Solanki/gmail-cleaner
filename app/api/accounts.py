"""
Accounts API Routes
--------------------
Endpoints for the multi-account switcher (Phase 4a).
"""

import logging

from fastapi import APIRouter, HTTPException, status

from app.models import AccountInfo, SwitchAccountRequest, SwitchAccountResponse
from app.services import accounts, get_gmail_service, switch_active_account

router = APIRouter(prefix="/api/accounts", tags=["Accounts"])
logger = logging.getLogger(__name__)


@router.get("")
async def api_list_accounts() -> list[AccountInfo]:
    """List every Gmail account authorized against this instance."""
    active = accounts.get_active_account()
    return [
        AccountInfo(email=email, active=email == active)
        for email in accounts.list_accounts()
    ]


@router.post("/switch")
async def api_switch_account(request: SwitchAccountRequest) -> SwitchAccountResponse:
    """Switch the active account. Target must already be authorized."""
    result = switch_active_account(request.email)
    return SwitchAccountResponse(**result)


@router.post("/add")
async def api_add_account():
    """Start OAuth for a brand-new account, without disturbing the active one.

    Mirrors POST /api/sign-in's shape (see app/api/actions.py) - calls
    get_gmail_service(add_new_account=True) inline so a genuine failure
    (e.g. a sign-in already in progress) is surfaced immediately, while the
    browser-based OAuth flow itself still runs in a background thread.
    """
    try:
        _service, error = get_gmail_service(add_new_account=True)
    except Exception as e:
        logger.exception("Error starting add-account sign-in")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to start sign-in",
        ) from e

    if error:
        if error.startswith("Sign-in started"):
            return {"status": "signing_in"}
        return {"status": "error", "error": error}

    return {"status": "signed_in"}
