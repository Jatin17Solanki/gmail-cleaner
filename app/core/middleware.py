"""
Auth Gate Middleware
---------------------
Rejects any request that lacks a valid app-login session, once APP_PASSWORD
is configured (Phase 1.3, resolves #109/#108/#111). Runs in front of every
route, including the Gmail OAuth sign-in flow — the shared password gates
access to the tool itself, not just its Gmail-facing actions.
"""

from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from app.core.security import SESSION_COOKIE_NAME, is_auth_enabled, is_valid_session

# Paths reachable without a session — just enough to render the login page
# and submit credentials. Static assets stay public too (login.html depends
# on them, and none of it is sensitive).
_PUBLIC_PATHS = {"/login", "/api/login", "/api/logout"}
_PUBLIC_PREFIXES = ("/static/",)


class AuthGateMiddleware(BaseHTTPMiddleware):
    """Redirects (HTML) or 401s (API) any unauthenticated request when auth is enabled."""

    async def dispatch(self, request: Request, call_next) -> Response:
        if not is_auth_enabled():
            return await call_next(request)

        path = request.url.path
        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        token = request.cookies.get(SESSION_COOKIE_NAME)
        if not is_valid_session(token):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(url=f"/login?next={quote(path)}")

        return await call_next(request)
