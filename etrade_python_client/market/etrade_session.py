"""
E*TRADE OAuth session manager.

Handles authentication and provides a reusable session for API calls.
The session is created once via browser-based OAuth and persisted in memory.
For the web dashboard, authentication is triggered via an API endpoint.
"""

import os
import logging
import configparser
import webbrowser
from rauth import OAuth1Service

logger = logging.getLogger("my_logger")

# Resolve config.ini relative to the etrade_python_client directory
_base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
config = configparser.ConfigParser()
config.read(os.path.join(_base_dir, "config.ini"))


class ETradeSession:
    """Manages an authenticated E*TRADE OAuth session."""

    def __init__(self):
        self.session = None
        self.base_url = None
        self._authenticated = False

    @property
    def is_authenticated(self) -> bool:
        return self._authenticated and self.session is not None

    def get_keys(self, use_sandbox: bool = False) -> tuple[str, str]:
        """Return the appropriate consumer key/secret pair."""
        if use_sandbox:
            return (
                config["DEFAULT"]["CONSUMER_KEY"],
                config["DEFAULT"]["CONSUMER_SECRET"],
            )
        # Try production keys first
        prod_key = config["DEFAULT"].get("PROD_CONSUMER_KEY", "")
        prod_secret = config["DEFAULT"].get("PROD_CONSUMER_SECRET", "")
        if prod_key and "PASTE" not in prod_key:
            return prod_key, prod_secret
        return (
            config["DEFAULT"]["CONSUMER_KEY"],
            config["DEFAULT"]["CONSUMER_SECRET"],
        )

    def start_auth(self, use_sandbox: bool = False) -> str:
        """
        Begin OAuth flow. Returns the authorization URL the user must visit.
        Call complete_auth() with the verification code after.
        """
        consumer_key, consumer_secret = self.get_keys(use_sandbox)

        self._oauth_service = OAuth1Service(
            name="etrade",
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            request_token_url="https://api.etrade.com/oauth/request_token",
            access_token_url="https://api.etrade.com/oauth/access_token",
            authorize_url="https://us.etrade.com/e/t/etws/authorize?key={}&token={}",
            base_url="https://api.etrade.com",
        )

        self.base_url = (
            config["DEFAULT"]["SANDBOX_BASE_URL"] if use_sandbox
            else config["DEFAULT"]["PROD_BASE_URL"]
        )

        self._request_token, self._request_token_secret = (
            self._oauth_service.get_request_token(
                params={"oauth_callback": "oob", "format": "json"}
            )
        )

        auth_url = self._oauth_service.authorize_url.format(
            self._oauth_service.consumer_key, self._request_token
        )
        logger.info("E*TRADE auth URL generated: %s", auth_url)
        return auth_url

    def complete_auth(self, verification_code: str) -> bool:
        """Complete OAuth with the verification code from the browser."""
        try:
            self.session = self._oauth_service.get_auth_session(
                self._request_token,
                self._request_token_secret,
                params={"oauth_verifier": verification_code.strip()},
            )
            self._authenticated = True
            logger.info("E*TRADE session authenticated successfully")
            return True
        except Exception as exc:
            logger.error("E*TRADE auth failed: %s", exc)
            self._authenticated = False
            return False

    def authenticate_interactive(self, use_sandbox: bool = False) -> bool:
        """Full interactive OAuth flow (for CLI usage)."""
        auth_url = self.start_auth(use_sandbox=use_sandbox)
        webbrowser.open(auth_url)
        code = input("\nPaste the E*TRADE verification code: ")
        return self.complete_auth(code)

    def get(self, url: str, **kwargs):
        """Make an authenticated GET request."""
        if not self.is_authenticated:
            logger.error("Not authenticated — cannot make API call")
            return None
        return self.session.get(url, header_auth=True, **kwargs)


# Global singleton — shared across the app
etrade_session = ETradeSession()
