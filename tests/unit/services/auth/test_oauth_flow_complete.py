"""
Tests for the OAuth Flow (Phase 1 bugfix)
--------------------------------------------
Tests for _run_oauth_and_save_token(), _perform_oauth_flow(),
_run_oauth_callback_server(), and _wait_for_callback() - the pieces that
replaced the previous branching between a manually-managed callback server
(with a timeout) and google-auth-oauthlib's Flow.run_local_server() (with
no timeout at all, the root cause of sign-in getting permanently stuck if
the user closed the consent tab without finishing).

_run_oauth_and_save_token() is now a standalone top-level function (it used
to be a closure spawned directly into a background thread from
get_gmail_service()), so it can be called synchronously here instead of
through a real thread - the previous version of this file asserted on
background-thread side effects with no synchronization, which is why
test_oauth_flow_desktop_mode_binds_to_localhost was flaky.
"""

import threading
from unittest.mock import Mock, mock_open, patch

import pytest

from app.services import auth


class TestRunOauthAndSaveToken:
    """Tests for _run_oauth_and_save_token(), called directly (synchronously)
    rather than through the background thread get_gmail_service() spawns it
    in."""

    @patch("app.services.auth.settings")
    @patch("app.services.auth._perform_oauth_flow")
    @patch("app.services.auth.InstalledAppFlow")
    @patch("app.services.auth.is_web_auth_mode", return_value=False)
    def test_saves_token_on_success(
        self, mock_web_auth, mock_flow, mock_perform_flow, mock_settings
    ):
        """A successful flow should write the returned credentials to token_file."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "localhost"
        mock_settings.oauth_external_port = None

        mock_flow.from_client_secrets_file.return_value = Mock()

        mock_creds = Mock()
        mock_creds.to_json.return_value = '{"token": "new_token"}'
        mock_perform_flow.return_value = mock_creds

        mock_file = mock_open()
        with patch("builtins.open", mock_file):
            auth._run_oauth_and_save_token("credentials.json")

        # assert_any_call rather than assert_called_once_with: builtins.open
        # is patched process-wide, so a stray daemon thread left running by
        # an unrelated, loosely-mocked test elsewhere in the suite (e.g.
        # test_credentials_handling_complete.py, which doesn't mock
        # _perform_oauth_flow) can add extra calls here. That's a pre-
        # existing test-isolation gap in how get_gmail_service()'s
        # background thread is (not) tracked in tests, not something this
        # test itself should need to solve.
        mock_file().write.assert_any_call('{"token": "new_token"}')

    @patch("app.services.auth._auth_in_progress", {"active": True})
    @patch("app.services.auth.settings")
    @patch("app.services.auth._perform_oauth_flow")
    @patch("app.services.auth.InstalledAppFlow")
    @patch("app.services.auth.is_web_auth_mode", return_value=False)
    def test_releases_lock_on_success(
        self, mock_web_auth, mock_flow, mock_perform_flow, mock_settings
    ):
        """The in-progress lock should always be released when the flow finishes."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "localhost"
        mock_settings.oauth_external_port = None

        mock_flow.from_client_secrets_file.return_value = Mock()
        mock_creds = Mock()
        mock_creds.to_json.return_value = "{}"
        mock_perform_flow.return_value = mock_creds

        with patch("builtins.open", mock_open()):
            auth._run_oauth_and_save_token("credentials.json")

        assert auth._auth_in_progress["active"] is False

    @patch("app.services.auth.settings")
    @patch("app.services.auth._perform_oauth_flow")
    @patch("app.services.auth.InstalledAppFlow")
    @patch("app.services.auth.is_web_auth_mode", return_value=False)
    def test_desktop_mode_binds_to_localhost(
        self, mock_web_auth, mock_flow, mock_perform_flow, mock_settings
    ):
        """Desktop mode (not web-auth) should bind the callback server to localhost."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "localhost"
        mock_settings.oauth_external_port = None

        mock_flow.from_client_secrets_file.return_value = Mock()
        mock_perform_flow.return_value = None  # short-circuits before token save

        with patch("builtins.open", mock_open()):
            auth._run_oauth_and_save_token("credentials.json")

        mock_perform_flow.assert_called_once()
        call_kwargs = mock_perform_flow.call_args.kwargs
        assert call_kwargs["bind_address"] == "localhost"
        assert call_kwargs["redirect_uri"] == "http://localhost:8767/"
        assert call_kwargs["port"] == 8767
        assert call_kwargs["timeout_seconds"] == auth.OAUTH_CALLBACK_TIMEOUT_SECONDS

    @patch("app.services.auth.settings")
    @patch("app.services.auth._perform_oauth_flow")
    @patch("app.services.auth.InstalledAppFlow")
    @patch("app.services.auth.is_web_auth_mode", return_value=True)
    def test_web_auth_mode_binds_to_all_interfaces(
        self, mock_web_auth, mock_flow, mock_perform_flow, mock_settings
    ):
        """Web-auth mode (Docker) should bind the callback server to 0.0.0.0."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "localhost"
        mock_settings.oauth_external_port = None

        mock_flow.from_client_secrets_file.return_value = Mock()
        mock_perform_flow.return_value = None

        with patch("builtins.open", mock_open()):
            auth._run_oauth_and_save_token("credentials.json")

        call_kwargs = mock_perform_flow.call_args.kwargs
        assert call_kwargs["bind_address"] == "0.0.0.0"

    @patch("app.services.auth.settings")
    @patch("app.services.auth._perform_oauth_flow")
    @patch("app.services.auth.InstalledAppFlow")
    @patch("app.services.auth.is_web_auth_mode", return_value=False)
    def test_custom_oauth_host_used_in_redirect_uri(
        self, mock_web_auth, mock_flow, mock_perform_flow, mock_settings
    ):
        """A custom OAUTH_HOST should be reflected in the redirect URI."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "custom.example.com"
        mock_settings.oauth_external_port = None

        mock_flow.from_client_secrets_file.return_value = Mock()
        mock_perform_flow.return_value = None

        with patch("builtins.open", mock_open()):
            auth._run_oauth_and_save_token("credentials.json")

        call_kwargs = mock_perform_flow.call_args.kwargs
        assert call_kwargs["redirect_uri"] == "http://custom.example.com:8767/"

    @patch("app.services.auth.settings")
    @patch("app.services.auth._perform_oauth_flow")
    @patch("app.services.auth.InstalledAppFlow")
    @patch("app.services.auth.is_web_auth_mode", return_value=False)
    def test_custom_external_port_used_in_redirect_uri_only(
        self, mock_web_auth, mock_flow, mock_perform_flow, mock_settings
    ):
        """A custom OAUTH_EXTERNAL_PORT (Docker port mapping) should appear in
        the redirect URI while the server still listens on the internal port."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "localhost"
        mock_settings.oauth_external_port = 18767

        mock_flow.from_client_secrets_file.return_value = Mock()
        mock_perform_flow.return_value = None

        with patch("builtins.open", mock_open()):
            auth._run_oauth_and_save_token("credentials.json")

        call_kwargs = mock_perform_flow.call_args.kwargs
        assert call_kwargs["redirect_uri"] == "http://localhost:18767/"
        assert call_kwargs["port"] == 8767

    @patch("app.services.auth._auth_in_progress", {"active": True})
    @patch("app.services.auth.settings")
    @patch("app.services.auth._perform_oauth_flow")
    @patch("app.services.auth.InstalledAppFlow")
    @patch("app.services.auth.is_web_auth_mode", return_value=False)
    def test_invalid_authorization_code_does_not_raise(
        self, mock_web_auth, mock_flow, mock_perform_flow, mock_settings
    ):
        """A ValueError from the callback flow should be caught, not propagate,
        and the in-progress lock should still be released."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "localhost"
        mock_settings.oauth_external_port = None

        mock_flow.from_client_secrets_file.return_value = Mock()
        mock_perform_flow.side_effect = ValueError("Invalid authorization code")

        auth._run_oauth_and_save_token("credentials.json")  # must not raise

        assert auth._auth_in_progress["active"] is False

    @patch("app.services.auth._auth_in_progress", {"active": True})
    @patch("app.services.auth.settings")
    @patch("app.services.auth._perform_oauth_flow")
    @patch("app.services.auth.InstalledAppFlow")
    @patch("app.services.auth.is_web_auth_mode", return_value=False)
    def test_timeout_does_not_raise_and_releases_lock(
        self, mock_web_auth, mock_flow, mock_perform_flow, mock_settings
    ):
        """A TimeoutError from the callback flow should be caught and the
        in-progress lock released, so a retry becomes possible - this is the
        behavior that fixes sign-in getting permanently stuck."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "localhost"
        mock_settings.oauth_external_port = None

        mock_flow.from_client_secrets_file.return_value = Mock()
        mock_perform_flow.side_effect = TimeoutError("OAuth flow timed out")

        auth._run_oauth_and_save_token("credentials.json")

        assert auth._auth_in_progress["active"] is False

    @patch("app.services.auth._auth_in_progress", {"active": True})
    @patch("app.services.auth.settings")
    @patch("app.services.auth._perform_oauth_flow")
    @patch("app.services.auth.InstalledAppFlow")
    @patch("app.services.auth.is_web_auth_mode", return_value=False)
    def test_generic_exception_still_releases_lock(
        self, mock_web_auth, mock_flow, mock_perform_flow, mock_settings
    ):
        """Any unexpected exception should still release the in-progress lock."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "localhost"
        mock_settings.oauth_external_port = None

        mock_flow.from_client_secrets_file.return_value = Mock()
        mock_perform_flow.side_effect = Exception("OAuth error")

        auth._run_oauth_and_save_token("credentials.json")

        assert auth._auth_in_progress["active"] is False

    @patch("app.services.auth.settings")
    @patch("app.services.auth.InstalledAppFlow")
    def test_empty_oauth_host_raises_before_starting_server(
        self, mock_flow, mock_settings
    ):
        """An empty OAUTH_HOST can't build a redirect URI - should fail fast
        with a clear message rather than starting a server that can never
        succeed."""
        mock_settings.token_file = "token.json"
        mock_settings.scopes = ["scope1", "scope2"]
        mock_settings.oauth_port = 8767
        mock_settings.oauth_host = "   "
        mock_settings.oauth_external_port = None

        mock_flow.from_client_secrets_file.return_value = Mock()

        # Should not raise out of the function (caught by the outer except),
        # and should not attempt to save a token.
        with patch("builtins.open", mock_open()) as mock_file:
            auth._run_oauth_and_save_token("credentials.json")
            mock_file.assert_not_called()


class TestPerformOauthFlow:
    """Tests for _perform_oauth_flow(), mocking _run_oauth_callback_server
    so these focus purely on URL/state handling and token exchange."""

    @patch("app.services.auth._run_oauth_callback_server")
    def test_success_exchanges_code_and_returns_credentials(
        self, mock_run_callback_server
    ):
        mock_run_callback_server.return_value = ("test_auth_code", None)

        flow = Mock()
        flow.authorization_url.return_value = (
            "https://accounts.google.com/auth",
            "csrf-state",
        )
        flow.credentials = Mock()

        result = auth._perform_oauth_flow(
            flow,
            bind_address="localhost",
            port=8767,
            redirect_uri="http://localhost:8767/",
            open_browser=False,
            timeout_seconds=5,
        )

        flow.fetch_token.assert_called_once_with(code="test_auth_code")
        assert result is flow.credentials
        assert flow.redirect_uri == "http://localhost:8767/"

    @patch("app.services.auth._run_oauth_callback_server")
    def test_missing_authorization_url_raises(self, mock_run_callback_server):
        flow = Mock()
        flow.authorization_url.return_value = (None, "csrf-state")

        with pytest.raises(
            ValueError, match="Failed to generate OAuth authorization URL"
        ):
            auth._perform_oauth_flow(
                flow,
                bind_address="localhost",
                port=8767,
                redirect_uri="http://localhost:8767/",
                open_browser=False,
                timeout_seconds=5,
            )

        mock_run_callback_server.assert_not_called()

    @patch("app.services.auth._run_oauth_callback_server")
    def test_oauth_error_response_raises(self, mock_run_callback_server):
        mock_run_callback_server.return_value = (None, "access_denied")

        flow = Mock()
        flow.authorization_url.return_value = (
            "https://accounts.google.com/auth",
            "csrf-state",
        )

        with pytest.raises(ValueError, match="access_denied"):
            auth._perform_oauth_flow(
                flow,
                bind_address="localhost",
                port=8767,
                redirect_uri="http://localhost:8767/",
                open_browser=False,
                timeout_seconds=5,
            )

    @patch("app.services.auth._run_oauth_callback_server")
    def test_missing_code_raises(self, mock_run_callback_server):
        mock_run_callback_server.return_value = (None, None)

        flow = Mock()
        flow.authorization_url.return_value = (
            "https://accounts.google.com/auth",
            "csrf-state",
        )

        with pytest.raises(ValueError, match="No authorization code received"):
            auth._perform_oauth_flow(
                flow,
                bind_address="localhost",
                port=8767,
                redirect_uri="http://localhost:8767/",
                open_browser=False,
                timeout_seconds=5,
            )

    @patch("app.services.auth._run_oauth_callback_server")
    def test_token_exchange_failure_raises(self, mock_run_callback_server):
        mock_run_callback_server.return_value = ("test_auth_code", None)

        flow = Mock()
        flow.authorization_url.return_value = (
            "https://accounts.google.com/auth",
            "csrf-state",
        )
        flow.fetch_token.side_effect = Exception("invalid_grant")

        with pytest.raises(ValueError, match="Failed to exchange authorization code"):
            auth._perform_oauth_flow(
                flow,
                bind_address="localhost",
                port=8767,
                redirect_uri="http://localhost:8767/",
                open_browser=False,
                timeout_seconds=5,
            )

    @patch("app.services.auth._run_oauth_callback_server")
    def test_timeout_propagates(self, mock_run_callback_server):
        mock_run_callback_server.side_effect = TimeoutError(
            "OAuth authorization timed out"
        )

        flow = Mock()
        flow.authorization_url.return_value = (
            "https://accounts.google.com/auth",
            "csrf-state",
        )

        with pytest.raises(TimeoutError):
            auth._perform_oauth_flow(
                flow,
                bind_address="localhost",
                port=8767,
                redirect_uri="http://localhost:8767/",
                open_browser=False,
                timeout_seconds=5,
            )

    @patch("app.services.auth._run_oauth_callback_server")
    def test_opens_browser_when_requested(self, mock_run_callback_server):
        mock_run_callback_server.return_value = ("test_auth_code", None)

        flow = Mock()
        flow.authorization_url.return_value = (
            "https://accounts.google.com/auth",
            "csrf-state",
        )

        with patch("webbrowser.open") as mock_open_browser:
            auth._perform_oauth_flow(
                flow,
                bind_address="localhost",
                port=8767,
                redirect_uri="http://localhost:8767/",
                open_browser=True,
                timeout_seconds=5,
            )

        mock_open_browser.assert_called_once_with("https://accounts.google.com/auth")

    @patch("app.services.auth._run_oauth_callback_server")
    def test_does_not_open_browser_when_not_requested(self, mock_run_callback_server):
        mock_run_callback_server.return_value = ("test_auth_code", None)

        flow = Mock()
        flow.authorization_url.return_value = (
            "https://accounts.google.com/auth",
            "csrf-state",
        )

        with patch("webbrowser.open") as mock_open_browser:
            auth._perform_oauth_flow(
                flow,
                bind_address="localhost",
                port=8767,
                redirect_uri="http://localhost:8767/",
                open_browser=False,
                timeout_seconds=5,
            )

        mock_open_browser.assert_not_called()


class TestRunOauthCallbackServer:
    """Tests for _run_oauth_callback_server(): server lifecycle, port
    conflicts, and reading back what OAuthCallbackHandler wrote."""

    @patch("app.services.auth.HTTPServer")
    def test_port_already_in_use_raises_clear_error(self, mock_http_server_cls):
        err = OSError("Address already in use")
        err.errno = 98
        mock_http_server_cls.side_effect = err

        with pytest.raises(OSError, match="already in use"):
            auth._run_oauth_callback_server("localhost", 8767, timeout_seconds=5)

    @patch("app.services.auth._wait_for_callback")
    @patch("app.services.auth.HTTPServer")
    def test_closes_server_even_when_wait_raises(self, mock_http_server_cls, mock_wait):
        mock_server = Mock()
        mock_http_server_cls.return_value = mock_server
        mock_wait.side_effect = TimeoutError("timed out")

        with pytest.raises(TimeoutError):
            auth._run_oauth_callback_server("localhost", 8767, timeout_seconds=0.01)

        mock_server.server_close.assert_called_once()

    @patch("app.services.auth.OAuthCallbackHandler")
    @patch("app.services.auth.HTTPServer")
    def test_returns_code_written_by_handler(
        self, mock_http_server_cls, mock_handler_cls
    ):
        """Simulates a real callback by invoking the same handler_factory the
        real HTTPServer would, with a fake handler that writes callback_data
        directly instead of parsing a real HTTP request/socket."""
        mock_server = Mock()
        mock_http_server_cls.return_value = mock_server

        def handle_request_side_effect():
            _, handler_factory = mock_http_server_cls.call_args[0]
            handler_factory(Mock(), ("127.0.0.1", 55555), mock_server)

        mock_server.handle_request.side_effect = handle_request_side_effect

        def fake_handler_init(
            callback_event, callback_lock, callback_data, *args, **kwargs
        ):
            with callback_lock:
                callback_data["code"] = "test_auth_code"
            callback_event.set()

        mock_handler_cls.side_effect = fake_handler_init

        auth_code, error = auth._run_oauth_callback_server(
            "localhost", 8767, timeout_seconds=5
        )

        assert auth_code == "test_auth_code"
        assert error is None
        mock_server.server_close.assert_called_once()


class TestWaitForCallback:
    """Tests for _wait_for_callback(): the pure polling loop, fully
    deterministic since callback_event is a real threading.Event this test
    controls directly."""

    def test_returns_once_event_is_set(self):
        server = Mock()
        event = threading.Event()
        calls = {"count": 0}

        def handle_request_side_effect():
            calls["count"] += 1
            if calls["count"] == 2:
                event.set()

        server.handle_request.side_effect = handle_request_side_effect

        auth._wait_for_callback(server, event, timeout_seconds=5)

        assert event.is_set()
        assert calls["count"] == 2

    def test_raises_timeout_error_if_event_never_set(self):
        server = Mock()
        event = threading.Event()  # never set
        server.handle_request.side_effect = lambda: None

        with pytest.raises(TimeoutError, match="timed out"):
            auth._wait_for_callback(server, event, timeout_seconds=0.05)

    def test_reraises_port_conflict_from_handle_request(self):
        server = Mock()
        event = threading.Event()
        err = OSError("Address already in use")
        err.errno = 98
        server.handle_request.side_effect = err

        with pytest.raises(OSError, match="already in use"):
            auth._wait_for_callback(server, event, timeout_seconds=5)

    def test_swallows_non_critical_handler_errors_and_keeps_polling(self):
        server = Mock()
        event = threading.Event()
        calls = {"count": 0}

        def handle_request_side_effect():
            calls["count"] += 1
            if calls["count"] == 1:
                raise ValueError("transient parsing error")
            event.set()

        server.handle_request.side_effect = handle_request_side_effect

        auth._wait_for_callback(server, event, timeout_seconds=5)

        assert event.is_set()
        assert calls["count"] == 2
