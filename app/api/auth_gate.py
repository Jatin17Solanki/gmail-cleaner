"""
Auth Gate API Routes
---------------------
Login/logout for the app's shared-password session gate (Phase 1.3).
Distinct from /api/sign-in and /api/sign-out, which are Gmail OAuth.
"""

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status

from app.core.security import (
    SESSION_COOKIE_NAME,
    SESSION_TTL_SECONDS,
    create_session,
    destroy_session,
    is_auth_enabled,
    verify_password,
)
from app.models import LoginRequest

router = APIRouter(prefix="/api", tags=["Auth Gate"])
logger = logging.getLogger(__name__)


@router.post("/login")
async def api_login(request: LoginRequest, response: Response):
    """Verify the shared password and start a session."""
    if not is_auth_enabled():
        return {"success": True}

    if not verify_password(request.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password",
        )

    token = create_session()
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=SESSION_TTL_SECONDS,
    )
    return {"success": True}


@router.post("/logout")
async def api_logout(request: Request, response: Response):
    """End the current session, if any."""
    destroy_session(request.cookies.get(SESSION_COOKIE_NAME))
    response.delete_cookie(SESSION_COOKIE_NAME)
    return {"success": True}
