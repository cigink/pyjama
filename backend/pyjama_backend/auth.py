"""Auth orchestration — session + interactive U2M flow + refresh.

Owns the in-memory access token, runs the loopback OAuth flow, refreshes before
remote calls, and stores refresh material in the OS keystore.
"""

from __future__ import annotations

import threading
import time
import webbrowser
from dataclasses import dataclass, field

from . import oauth
from .config import DatabricksConfig
from .keystore import KeyStore
from .logging_setup import Secret, log, new_operation_id
from .pkce import Pkce

REFRESH_KEY = "databricks-refresh"
REFRESH_SKEW = 120.0  # refresh when within 2 min of expiry


class AuthError(Exception):
    pass


class NotAuthenticated(AuthError):
    pass


class SessionExpired(AuthError):
    pass


@dataclass
class Session:
    workspace_url: str
    user_subject: str
    access_token: Secret
    expires_at: float
    scopes: list[str] = field(default_factory=list)

    def needs_refresh(self, now: float | None = None) -> bool:
        now = now if now is not None else time.time()
        return (self.expires_at - now) <= REFRESH_SKEW


@dataclass
class AccessContext:
    base: str
    token: Secret


class AuthService:
    def __init__(self, config: DatabricksConfig, keystore: KeyStore):
        self._config = config
        self._keystore = keystore
        self._session: Session | None = None
        self._lock = threading.Lock()

    @property
    def config(self) -> DatabricksConfig:
        return self._config

    def is_authenticated(self) -> bool:
        with self._lock:
            return self._session is not None

    def connect(self) -> dict:
        """Run the full interactive authorization-code + PKCE flow."""
        base = self._config.base_url()
        op = new_operation_id()
        log("starting oauth u2m flow", operation_id=op)

        pkce = Pkce.generate()
        redirect_uri = oauth.REDIRECT_URI
        url = oauth.authorize_url(base, self._config.client_id, redirect_uri, pkce.challenge, pkce.state)

        # Start the loopback capture before opening the browser.
        result: dict = {}
        error: dict = {}

        def capture():
            try:
                result["redirect"] = oauth.capture_one_redirect(oauth.REDIRECT_PORT)
            except Exception as e:  # noqa: BLE001
                error["err"] = e

        t = threading.Thread(target=capture, daemon=True)
        t.start()
        time.sleep(0.2)
        webbrowser.open(url)
        t.join(timeout=300)

        if error:
            raise AuthError(f"loopback error: {error['err']}")
        if "redirect" not in result:
            raise AuthError("sign-in timed out")

        code = oauth.extract_code(result["redirect"], pkce.state)
        tokens = oauth.exchange_code(base, self._config.client_id, code, pkce.verifier, redirect_uri)

        if tokens.refresh_token:
            self._keystore.set(REFRESH_KEY, tokens.refresh_token.expose())

        scopes = tokens.scope.split(" ") if tokens.scope else []
        with self._lock:
            self._session = Session(
                workspace_url=self._config.workspace_url,
                user_subject="databricks-user",
                access_token=tokens.access_token,
                expires_at=tokens.expires_at,
                scopes=scopes,
            )
        log("oauth u2m flow complete", operation_id=op)
        return {
            "workspace_url": self._config.workspace_url,
            "user_subject": "databricks-user",
            "scopes": scopes,
        }

    def try_restore(self) -> bool:
        """Re-establish a session from a cached refresh token (session survives
        app restart — P1.3). Best-effort; returns False if no token or refresh
        fails."""
        refresh = self._keystore.get(REFRESH_KEY)
        if not refresh:
            return False
        try:
            base = self._config.base_url()
            tokens = oauth.refresh_tokens(base, self._config.client_id, Secret(refresh))
        except Exception:  # noqa: BLE001
            return False
        if tokens.refresh_token:
            self._keystore.set(REFRESH_KEY, tokens.refresh_token.expose())
        scopes = tokens.scope.split(" ") if tokens.scope else []
        with self._lock:
            self._session = Session(
                workspace_url=self._config.workspace_url,
                user_subject="databricks-user",
                access_token=tokens.access_token,
                expires_at=tokens.expires_at,
                scopes=scopes,
            )
        log("session restored from cached refresh token")
        return True

    def access_context(self) -> AccessContext:
        """Return base URL + fresh token, refreshing first if near expiry."""
        base = self._config.base_url()
        with self._lock:
            session = self._session
        if session is None:
            raise NotAuthenticated("not authenticated")

        if session.needs_refresh():
            refresh = self._keystore.get(REFRESH_KEY)
            if not refresh:
                raise SessionExpired("session expired; please sign in again")
            try:
                tokens = oauth.refresh_tokens(base, self._config.client_id, Secret(refresh))
            except Exception as e:  # noqa: BLE001
                raise SessionExpired("session expired; please sign in again") from e
            if tokens.refresh_token:
                self._keystore.set(REFRESH_KEY, tokens.refresh_token.expose())
            with self._lock:
                if self._session:
                    self._session.access_token = tokens.access_token
                    self._session.expires_at = tokens.expires_at
                    session = self._session

        return AccessContext(base=base, token=session.access_token)

    def logout(self) -> None:
        with self._lock:
            self._session = None
        self._keystore.delete(REFRESH_KEY)
        log("signed out; refresh credential removed")
