"""OAuth 2.0 U2M authorization-code + PKCE flow.

Databricks OIDC endpoints live at fixed paths under the workspace host:
  authorize: {host}/oidc/v1/authorize
  token:     {host}/oidc/v1/token

Exchange/refresh take the base URL so tests can point them at a mock server.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlparse, parse_qs

import requests

from .logging_setup import Secret

SCOPES = "all-apis offline_access"
# The built-in databricks-cli public client registers exactly this loopback URI.
REDIRECT_PORT = 8020
REDIRECT_URI = f"http://localhost:{REDIRECT_PORT}"


class OAuthError(Exception):
    pass


def authorize_url(base: str, client_id: str, redirect_uri: str, challenge: str, state: str) -> str:
    query = urlencode({
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    })
    return f"{base}/oidc/v1/authorize?{query}"


@dataclass
class Tokens:
    access_token: Secret
    refresh_token: Secret | None
    expires_at: float
    scope: str | None


def _post_token(base: str, data: dict) -> Tokens:
    resp = requests.post(f"{base}/oidc/v1/token", data=data, timeout=30)
    if not resp.ok:
        # Never echo the request body (holds the refresh token).
        raise OAuthError(f"token endpoint returned {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    return Tokens(
        access_token=Secret(body["access_token"]),
        refresh_token=Secret(body["refresh_token"]) if body.get("refresh_token") else None,
        expires_at=time.time() + int(body.get("expires_in", 3600)),
        scope=body.get("scope"),
    )


def exchange_code(base: str, client_id: str, code: str, verifier: Secret, redirect_uri: str) -> Tokens:
    return _post_token(base, {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": verifier.expose(),
    })


def refresh_tokens(base: str, client_id: str, refresh_token: Secret) -> Tokens:
    return _post_token(base, {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token.expose(),
        "client_id": client_id,
        "scope": SCOPES,
    })


def extract_code(redirect: str, expected_state: str) -> str:
    q = parse_qs(urlparse(redirect).query)
    if q.get("state", [None])[0] != expected_state:
        raise OAuthError("state mismatch on redirect (possible CSRF)")
    code = q.get("code", [None])[0]
    if not code:
        raise OAuthError("authorization redirect missing code")
    return code


def capture_one_redirect(port: int, timeout: float = 300.0) -> str:
    """Block until one loopback GET arrives; return its path+query as a URL."""
    import http.server
    import socketserver

    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            captured["path"] = self.path
            body = (
                b"<html><body style='font-family:sans-serif;text-align:center;padding-top:60px'>"
                b"<h2>PyJama</h2><p>Sign-in complete. You can close this tab.</p></body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence default stderr logging
            pass

    with socketserver.TCPServer(("127.0.0.1", port), Handler) as httpd:
        httpd.timeout = timeout
        httpd.handle_request()  # serve exactly one request

    if "path" not in captured:
        raise OAuthError("no loopback redirect received (timed out)")
    return f"http://localhost:{port}{captured['path']}"
