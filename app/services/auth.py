"""
Authentication Service
----------------------
Handles OAuth2 authentication with Gmail API.
"""

import json
import logging
import os
import platform
import shutil
import threading
import time
from http.server import HTTPServer

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from app.core import settings, state
from app.services.auth_handlers import OAuthCallbackHandler

logger = logging.getLogger(__name__)


# Track auth in progress, and when it started so a caller hitting the
# "already in progress" branch can be told how much longer to expect to
# wait, instead of a generic "up to N seconds" regardless of actual elapsed
# time.
_auth_in_progress = {"active": False, "started_at": None}

# How long to wait for the browser-based OAuth consent callback before
# giving up and releasing _auth_in_progress. Keep this in sync with the
# frontend's polling budget (GmailCleaner.Auth.pollStatus's maxAttempts in
# static/js/auth.js) so the UI doesn't give up on the user before the
# backend lock has had a chance to clear itself.
OAUTH_CALLBACK_TIMEOUT_SECONDS = 90


def _is_file_empty(file_path: str) -> bool:
    """Check if a file exists and is empty.

    Args:
        file_path: Path to the file to check.

    Returns:
        True if file exists and is empty, False otherwise.
    """
    if not os.path.exists(file_path):
        return False
    try:
        with open(file_path, "r") as f:
            content = f.read().strip()
            return not content
    except OSError:
        # If we can't read it, consider it not empty to avoid false positives
        return False


def is_web_auth_mode() -> bool:
    """Check if we should use web-based auth (for Docker/headless)."""
    return settings.web_auth


def needs_auth_setup() -> bool:
    """Check if authentication is needed."""
    if os.path.exists(settings.token_file):
        # Check if token file is empty
        if _is_file_empty(settings.token_file):
            logger.error(f"Token file {settings.token_file} is empty")
            try:
                os.remove(settings.token_file)
            except OSError:
                pass
            return True

        try:
            creds = Credentials.from_authorized_user_file(
                settings.token_file, settings.scopes
            )
            if creds and (creds.valid or creds.refresh_token):
                return False
        except (ValueError, OSError) as e:
            # Token file exists but is invalid/corrupted
            logger.warning(f"Failed to load credentials from token file: {e}")
        except Exception as e:
            # Unexpected error - log it for debugging
            logger.error(f"Unexpected error checking auth setup: {e}", exc_info=True)
    return True


def get_web_auth_status() -> dict:
    """Get current web auth status."""
    return {
        "needs_setup": needs_auth_setup(),
        "web_auth_mode": is_web_auth_mode(),
        "has_credentials": os.path.exists(settings.credentials_file),
        "pending_auth_url": state.pending_auth_url.get("url"),
    }


def _try_refresh_creds(creds: Credentials) -> Credentials | None:
    """Attempt to refresh expired credentials and save to token file.

    Args:
        creds: Credentials that are expired but have a refresh_token.

    Returns:
        Refreshed credentials if successful, None if refresh failed.
    """
    try:
        creds.refresh(Request())
        try:
            with open(settings.token_file, "w") as token:
                token.write(creds.to_json())
        except OSError:
            # Token file write failed - creds are refreshed in memory but not saved
            logger.exception("Failed to save refreshed token")
        return creds
    except RefreshError as e:
        # Refresh token is invalid or expired
        logger.warning(f"Token refresh failed: {e}")
        # Clear invalid token file
        try:
            os.remove(settings.token_file)
        except OSError:
            pass
        return None


def _get_credentials_path() -> str | None:
    """Get credentials - from file or create from env var.

    Returns:
        Path to valid credentials file, or None if not found or invalid.
    """
    if os.path.exists(settings.credentials_file):
        # Check if credentials file is empty
        if _is_file_empty(settings.credentials_file):
            logger.error(
                f"Credentials file {settings.credentials_file} is empty. "
                "Please check your credentials.json file and ensure it contains valid OAuth credentials."
            )
            return None

        # Validate that the file contains valid JSON
        try:
            with open(settings.credentials_file, "r") as f:
                content = f.read().strip()
                # Try to parse as JSON to validate
                json.loads(content)
            return settings.credentials_file
        except FileNotFoundError:
            # File was deleted between exists() check and open() - race condition
            # or test mocking issue - treat as if file doesn't exist
            return None
        except json.JSONDecodeError as e:
            logger.error(
                f"Credentials file {settings.credentials_file} contains invalid JSON: {e}",
                exc_info=True,
            )
            return None
        except OSError as e:
            logger.error(
                f"Failed to read credentials file {settings.credentials_file}: {e}",
                exc_info=True,
            )
            return None

    # Check for env var (for cloud deployment)
    env_creds = os.environ.get("GOOGLE_CREDENTIALS")
    if env_creds:  # Check if key exists and is not empty
        try:
            # Validate JSON before writing
            json.loads(env_creds)
            with open(settings.credentials_file, "w") as f:
                f.write(env_creds)
            return settings.credentials_file
        except (json.JSONDecodeError, TypeError):
            logger.error(
                "GOOGLE_CREDENTIALS environment variable contains invalid JSON/type",
                exc_info=True,
            )
            return None
        except OSError as e:
            logger.error(f"Failed to write credentials file: {e}", exc_info=True)
            # Don't create invalid file - return None
            return None

    return None


def _perform_oauth_flow(
    flow: InstalledAppFlow,
    *,
    bind_address: str,
    port: int,
    redirect_uri: str,
    open_browser: bool,
    timeout_seconds: float,
):
    """Run the browser-based OAuth consent flow and wait for its callback.

    Always uses a manually-managed local HTTP server with an explicit
    timeout, rather than google-auth-oauthlib's own Flow.run_local_server()
    - that call has no timeout of its own, so if the user closes the
    consent tab without finishing, it blocks forever and _auth_in_progress
    never clears (previously observable as sign-in being permanently
    "stuck" with no way to recover short of restarting the server).

    Returns:
        The obtained Credentials.

    Raises:
        ValueError: on a missing/invalid authorization URL, a CSRF/state
            mismatch (raised inside OAuthCallbackHandler), an OAuth error
            response, a missing authorization code, or a failed token
            exchange.
        TimeoutError: if no callback arrives within timeout_seconds.
        OSError: if the callback port is already in use.
    """
    flow.redirect_uri = redirect_uri

    authorization_url, oauth_state = flow.authorization_url(
        access_type="offline", prompt="consent"
    )
    if not authorization_url or not isinstance(authorization_url, str):
        raise ValueError(
            "Failed to generate OAuth authorization URL. "
            "Please check your credentials.json configuration."
        )

    with state.oauth_state_lock:
        state.oauth_state["state"] = oauth_state
    logger.debug(
        f"Stored OAuth state for CSRF protection: {oauth_state[:20]}..."
        if oauth_state and len(oauth_state) > 20
        else f"Stored OAuth state: {oauth_state}"
    )

    if is_web_auth_mode():
        state.pending_auth_url["url"] = authorization_url

    print(f"Please visit this URL to authorize the application: {authorization_url}")
    logger.info(f"OAuth authorization URL: {authorization_url}")

    if open_browser:
        try:
            import webbrowser

            webbrowser.open(authorization_url)
        except Exception as e:
            logger.warning(f"Failed to open browser: {e}")

    auth_code, error_message = _run_oauth_callback_server(
        bind_address, port, timeout_seconds
    )

    if error_message:
        raise ValueError(f"OAuth error: {error_message}")
    if not auth_code:
        raise ValueError("No authorization code received")

    try:
        flow.fetch_token(code=auth_code)
    except Exception as e:
        logger.error(
            f"Failed to exchange authorization code for token: {e}",
            exc_info=True,
        )
        raise ValueError(
            f"Failed to exchange authorization code: {str(e)}. "
            "Please try signing in again."
        ) from e

    return flow.credentials


def _run_oauth_callback_server(bind_address: str, port: int, timeout_seconds: float):
    """Start a local HTTP server and wait for the OAuth callback, or time out.

    Returns:
        (auth_code, error_message) - populated by OAuthCallbackHandler from
        the incoming callback request. On a clean success, error_message is
        None; on a CSRF/state mismatch or an OAuth error response, auth_code
        is None and error_message describes why.

    Raises:
        OSError: if the callback port is already in use.
        TimeoutError: if no callback arrives within timeout_seconds (see
            _wait_for_callback).
    """
    callback_event = threading.Event()
    callback_lock = threading.Lock()
    callback_data: dict = {"code": None, "error": None}

    def handler_factory(*args, **kwargs):
        return OAuthCallbackHandler(
            callback_event, callback_lock, callback_data, *args, **kwargs
        )

    server = None
    try:
        try:
            server = HTTPServer((bind_address, port), handler_factory)
        except OSError as e:
            error_str = str(e).lower()
            if "address already in use" in error_str or (
                hasattr(e, "errno") and e.errno in (98, 10048)
            ):  # Linux: 98, Windows: 10048
                raise OSError(
                    f"Port {port} is already in use. "
                    "Please stop any other service using this port or change the port configuration."
                ) from e
            raise

        _wait_for_callback(server, callback_event, timeout_seconds)

        with callback_lock:
            return callback_data["code"], callback_data["error"]
    finally:
        if server is not None:
            try:
                server.server_close()
            except Exception as e:
                logger.warning(f"Error closing OAuth callback server: {e}")


def _wait_for_callback(
    server: HTTPServer, callback_event, timeout_seconds: float
) -> None:
    """Poll `server` for a single OAuth callback request until `callback_event`
    is set or `timeout_seconds` elapses.

    Raises:
        TimeoutError: if no callback arrives in time.
        OSError: if handling a request hits a port conflict.
    """
    start_time = time.time()
    server.timeout = 1.0  # 1 second timeout for handle_request

    while not callback_event.is_set():
        elapsed = time.time() - start_time
        if elapsed > timeout_seconds:
            raise TimeoutError(
                f"OAuth authorization timed out after {timeout_seconds} seconds. "
                "Please try signing in again."
            )

        try:
            server.handle_request()
        except Exception as e:
            logger.error(f"Error handling OAuth callback request: {e}", exc_info=True)
            error_str = str(e).lower()
            if "address already in use" in error_str or (
                isinstance(e, OSError)
                and hasattr(e, "errno")
                and e.errno in (98, 10048)
            ):
                raise

        if not callback_event.wait(timeout=0.1):
            continue


def _run_oauth_and_save_token(creds_path: str) -> None:
    """Run the OAuth consent flow and persist the resulting token.

    Runs in its own daemon thread (see get_gmail_service()) so the web
    server stays responsive while waiting for the browser-based consent
    step. Always resets _auth_in_progress and the pending OAuth state in
    its finally block, regardless of outcome.
    """
    try:
        # Try to create the OAuth flow - this will fail if credentials.json is invalid
        try:
            flow = InstalledAppFlow.from_client_secrets_file(
                creds_path, settings.scopes
            )
        except (
            ValueError,
            json.JSONDecodeError,
            OSError,
            FileNotFoundError,
        ) as e:
            # This error happens when loading credentials file - definitely a credentials issue
            error_msg = str(e)
            logger.error(
                f"Failed to load credentials file {creds_path}: {e}",
                exc_info=True,
            )
            if (
                "Expecting value" in error_msg
                or "char 0" in error_msg
                or isinstance(e, json.JSONDecodeError)
            ):
                print(
                    "ERROR: credentials.json file is empty or contains invalid JSON. "
                    "Please check your credentials.json file and ensure it contains valid OAuth credentials."
                )
            elif isinstance(e, FileNotFoundError):
                print(
                    f"ERROR: credentials.json file not found at {creds_path}. "
                    "Please check your credentials.json file path."
                )
            else:
                print(
                    f"ERROR: Failed to read credentials file: {e}. "
                    "Please check your credentials.json file."
                )
            return  # Exit early - can't proceed without valid credentials

        # For Docker: bind to 0.0.0.0 so callback can reach container
        # For local: bind to localhost for security
        bind_address = "0.0.0.0" if is_web_auth_mode() else "localhost"  # nosec B104

        # Handle custom external port (e.g., Docker port mapping like 18767:8767)
        # The server listens on the internal port, but the redirect URI uses the external port
        redirect_port = (
            settings.oauth_external_port
            if isinstance(settings.oauth_external_port, int)
            else settings.oauth_port
        )

        # Validate port ranges (1-65535)
        if not isinstance(settings.oauth_port, int) or not (
            1 <= settings.oauth_port <= 65535
        ):
            raise ValueError(
                f"Invalid oauth_port: {settings.oauth_port}. "
                "Port must be between 1 and 65535."
            )
        if not isinstance(redirect_port, int) or not (1 <= redirect_port <= 65535):
            raise ValueError(
                f"Invalid redirect port: {redirect_port}. "
                "Port must be between 1 and 65535."
            )

        # Check if we should auto-open browser
        # In Docker/web mode: don't open browser, print URL to logs
        # On Windows/Mac/Linux desktop: auto-open browser
        if is_web_auth_mode():
            open_browser = False
        elif platform.system() == "Windows":
            open_browser = True
        elif platform.system() == "Darwin":  # macOS
            open_browser = True
        else:  # Linux
            open_browser = bool(shutil.which("xdg-open") or os.environ.get("DISPLAY"))

        # Validate oauth_host is not empty - needed to construct the
        # redirect URI below regardless of whether a custom external
        # port is in play
        if not settings.oauth_host or not settings.oauth_host.strip():
            raise ValueError(
                "oauth_host cannot be empty. "
                "Please set the OAUTH_HOST environment variable."
            )

        redirect_uri = f"http://{settings.oauth_host}:{redirect_port}/"
        if redirect_port != settings.oauth_port:
            logger.info(
                f"Using custom redirect URI {redirect_uri} "
                f"(internal port: {settings.oauth_port}, external port: {redirect_port})"
            )

        # Always use the manually-managed, timeout-bounded callback
        # server (see _perform_oauth_flow docstring) rather than
        # Flow.run_local_server(), which has no timeout and can
        # block _auth_in_progress open indefinitely if the user
        # closes the consent tab without finishing.
        new_creds = _perform_oauth_flow(
            flow,
            bind_address=bind_address,
            port=settings.oauth_port,
            redirect_uri=redirect_uri,
            open_browser=open_browser,
            timeout_seconds=OAUTH_CALLBACK_TIMEOUT_SECONDS,
        )

        # Validate credentials were obtained
        if new_creds is None:
            raise ValueError("OAuth flow completed but no credentials were obtained")

        # Save token with error handling
        try:
            with open(settings.token_file, "w") as token:
                token.write(new_creds.to_json())
            print("OAuth complete! Token saved.")
        except OSError as e:
            logger.error(f"Failed to save token file: {e}", exc_info=True)
            print(f"OAuth completed but failed to save token: {e}")
            raise  # Re-raise so outer exception handler can log it
    except (ValueError, json.JSONDecodeError) as e:
        # JSON parsing errors from OAuth callback (shouldn't happen if credentials were valid)
        error_msg = str(e)
        logger.error(
            "OAuth callback received empty or invalid response. "
            "This usually means the authorization was cancelled or the callback URL is incorrect.",
            exc_info=True,
        )
        print(
            "OAuth error: Authorization cancelled or invalid callback. "
            "Please try signing in again and complete the authorization in your browser."
        )
    except RefreshError as e:
        # Token refresh errors
        logger.error(f"OAuth token exchange failed: {e}", exc_info=True)
        print(
            "OAuth error: Token exchange failed. Please try again. "
            "If this persists, check your credentials.json configuration."
        )
    except TimeoutError as e:
        # Timeout errors
        logger.error(f"OAuth timeout: {e}", exc_info=True)
        print(f"OAuth error: {e}")
    except OSError as e:
        # Port binding and other OS errors
        logger.error(f"OAuth system error: {e}", exc_info=True)
        error_str = str(e)
        if "Address already in use" in error_str or "port" in error_str.lower():
            print(
                f"OAuth error: Port conflict - {e}. "
                "Please check if another service is using the OAuth port or change the port configuration."
            )
        else:
            print(f"OAuth error: System error - {e}")
    except Exception as e:
        # Other OAuth errors
        logger.error(f"OAuth error: {e}", exc_info=True)
        error_str = str(e)
        if "redirect_uri_mismatch" in error_str.lower():
            print(
                "OAuth error: Redirect URI mismatch. "
                "Please check your credentials.json redirect URI configuration."
            )
        elif "access_denied" in error_str.lower():
            print("OAuth error: Access denied. Please grant the requested permissions.")
        elif "invalid_grant" in error_str.lower():
            print(
                "OAuth error: Invalid authorization code. "
                "The authorization may have expired. Please try signing in again."
            )
        else:
            print(f"OAuth error: {e}")
    finally:
        # Always reset auth state, even on error
        _auth_in_progress["active"] = False
        _auth_in_progress["started_at"] = None
        state.pending_auth_url["url"] = None
        with state.oauth_state_lock:
            state.oauth_state["state"] = None


def get_gmail_service():
    """Get authenticated Gmail API service.

    Returns:
        tuple: (service, error_message) - service is None if auth needed
    """
    creds = None

    if os.path.exists(settings.token_file):
        # Check if token file is empty
        if _is_file_empty(settings.token_file):
            logger.error(f"Token file {settings.token_file} is empty")
            try:
                os.remove(settings.token_file)
            except OSError:
                pass
            creds = None
        else:
            try:
                creds = Credentials.from_authorized_user_file(
                    settings.token_file, settings.scopes
                )
            except (ValueError, OSError) as e:
                # Token file is corrupted or invalid
                logger.warning(f"Failed to load credentials from token file: {e}")
                # Delete corrupted token file
                try:
                    os.remove(settings.token_file)
                except OSError:
                    pass
                creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds = _try_refresh_creds(creds)

        # If creds is still None or invalid after refresh attempt, trigger OAuth
        if not creds or not creds.valid:
            # Prevent multiple OAuth attempts (thread-safe check)
            # Note: Small race condition window, but acceptable for this use case
            if _auth_in_progress.get("active", False):
                started_at = _auth_in_progress.get("started_at")
                elapsed = time.time() - started_at if started_at else 0
                remaining = max(0, round(OAUTH_CALLBACK_TIMEOUT_SECONDS - elapsed))
                return (
                    None,
                    "A previous sign-in attempt is still pending. If you closed "
                    "that browser tab, it will clear automatically in about "
                    f"{remaining} seconds - then try again.",
                )

            creds_path = _get_credentials_path()
            if not creds_path:
                # Check if credentials file exists and is empty for more specific error message
                if os.path.exists(settings.credentials_file) and _is_file_empty(
                    settings.credentials_file
                ):
                    return (
                        None,
                        "credentials.json file is empty! Please check your credentials.json file and ensure it contains valid OAuth credentials.",
                    )
                return (
                    None,
                    "credentials.json not found or contains invalid JSON! Please check your credentials.json file and follow setup instructions.",
                )

            # Start OAuth in background thread so server stays responsive
            _auth_in_progress["active"] = True
            _auth_in_progress["started_at"] = time.time()

            oauth_thread = threading.Thread(
                target=_run_oauth_and_save_token, args=(creds_path,), daemon=True
            )
            oauth_thread.start()

            return (
                None,
                "Sign-in started. Please complete authorization in your browser.",
            )

    # Build Gmail service - handle potential errors
    try:
        service = build("gmail", "v1", credentials=creds)
    except Exception as e:
        logger.error(f"Failed to build Gmail service: {e}", exc_info=True)
        # Return error instead of crashing
        return (
            None,
            f"Failed to connect to Gmail API: {str(e)}. Please try signing in again.",
        )

    try:
        profile = service.users().getProfile(userId="me").execute()
        state.current_user["email"] = profile.get("emailAddress", "Unknown")
        state.current_user["logged_in"] = True
    except Exception:
        state.current_user["email"] = "Unknown"
        state.current_user["logged_in"] = True

    return service, None


def sign_out() -> dict:
    """Sign out by removing the token file."""
    if os.path.exists(settings.token_file):
        os.remove(settings.token_file)

    # Reset state
    state.current_user = {"email": None, "logged_in": False}
    state.reset_scan()
    state.reset_delete_scan()
    state.reset_mark_read()

    print("Signed out - results cleared")
    return {
        "success": True,
        "message": "Signed out successfully",
        "results_cleared": True,
    }


def check_login_status() -> dict:
    """Check if user is logged in and get their email."""
    if os.path.exists(settings.token_file):
        # Check if token file is empty
        if _is_file_empty(settings.token_file):
            logger.error(f"Token file {settings.token_file} is empty")
            try:
                os.remove(settings.token_file)
            except OSError:
                pass
        else:
            try:
                creds = Credentials.from_authorized_user_file(
                    settings.token_file, settings.scopes
                )
                if creds and creds.valid:
                    service = build("gmail", "v1", credentials=creds)
                    profile = service.users().getProfile(userId="me").execute()
                    state.current_user["email"] = profile.get("emailAddress", "Unknown")
                    state.current_user["logged_in"] = True
                    return state.current_user.copy()
                elif creds and creds.expired and creds.refresh_token:
                    refreshed_creds = _try_refresh_creds(creds)
                    if refreshed_creds:
                        service = build("gmail", "v1", credentials=refreshed_creds)
                        profile = service.users().getProfile(userId="me").execute()
                        state.current_user["email"] = profile.get(
                            "emailAddress", "Unknown"
                        )
                        state.current_user["logged_in"] = True
                        return state.current_user.copy()
            except (ValueError, OSError) as e:
                # Token file is invalid/corrupted
                logger.warning(f"Failed to load or refresh credentials: {e}")
                # Clear corrupted token file
                try:
                    os.remove(settings.token_file)
                except OSError:
                    pass
            except Exception as e:
                # API errors, network issues, etc.
                logger.error(f"Error checking login status: {e}", exc_info=True)

    state.current_user["email"] = None
    state.current_user["logged_in"] = False
    return state.current_user.copy()
