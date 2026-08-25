"""Fake external OAuth2 client_credentials IdP + a bearer-gated data
endpoint, for testing the no-code REST connector's OAuth2 auth type.

Deliberately not Holon's own Identity service: `holon_common.
connector_safety` blocks every platform-internal hostname (identity,
connectivity, knowledge, ...) by design — a tenant connector must never
be able to reach the platform's own control plane. This fixture is a
genuinely separate, self-contained fake external system, the same role
`reviews-api` plays for the plain REST connector tests.

Stdlib only, no new dependency — same minimalism as reviews-api's
`python -m http.server`, just with enough real logic (client_credentials
validation, opaque bearer tokens) to prove the connector's full
token-fetch-and-attach loop against something real.
"""

from __future__ import annotations

import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

CLIENT_ID = "test-oauth2-client"
CLIENT_SECRET = "test-oauth2-secret"

_issued_tokens: set[str] = set()

_PRINCIPALS = [
    {"urn": "hl:acme:global:user:fake1", "display_name": "Fake One"},
    {"urn": "hl:acme:global:user:fake2", "display_name": "Fake Two"},
    {"urn": "hl:acme:global:user:fake3", "display_name": "Fake Three"},
]


class Handler(BaseHTTPRequestHandler):
    def _send_json(self, status: int, body) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:
        if self.path != "/token":
            self._send_json(404, {"error": "not_found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode())
        client_id = form.get("client_id", [""])[0]
        client_secret = form.get("client_secret", [""])[0]
        if client_id != CLIENT_ID or client_secret != CLIENT_SECRET:
            self._send_json(401, {"error": "invalid_client"})
            return
        token = secrets.token_hex(16)
        _issued_tokens.add(token)
        self._send_json(200, {"access_token": token, "token_type": "bearer", "expires_in": 3600})

    def do_GET(self) -> None:
        if self.path != "/principals":
            self._send_json(404, {"error": "not_found"})
            return
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if not token or token not in _issued_tokens:
            self._send_json(401, {"error": "invalid_token"})
            return
        self._send_json(200, _PRINCIPALS)

    def log_message(self, format: str, *args) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
