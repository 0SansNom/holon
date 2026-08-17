"""A real client SDK — the "products/SDK" gap this build otherwise left
open (Experience's own web app was the only thing ever built against the
platform's HTTP API). Standard library only — any Python 3.9+, no extra
install — consolidating the token+JSON request helper, poll-with-retry
login, and sync-then-poll-catalog wait that used to be hand-rolled across
tests and CLI.

Deliberately thin: no retries/backoff policy beyond what every call site
already did by hand, no service-URL registry (callers keep passing full
URLs, exactly as before) — this is a consolidation of proven, duplicated
code, not a new abstraction layer speculatively built out further than
what already existed three-plus times over.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

__all__ = ["HolonClient"]


class HolonClient:
    def __init__(self, *, identity_url: str, timeout: float = 30.0):
        self.identity_url = identity_url
        self.timeout = timeout

    def request(
        self, method: str, url: str, *, token: Optional[str] = None, body: Optional[dict] = None
    ) -> tuple[int, Any]:
        """Never raises on an HTTP error response — every existing call
        site already handled a non-2xx status as data (an expected
        403/404/400 in an authorization or validation test), not an
        exception. Callers that want a hard failure on unexpected status
        just assert on it, the same as every test file already does.
        """
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def token_for(self, principal_urn: str, *, client_secret: Optional[str] = None, timeout: float = 60.0) -> str:
        """Polls, not a single attempt: Identity may not be up yet (a
        fresh `make up`) or a just-seeded principal may not have
        propagated — every prior copy of this retried for exactly this
        reason. `client_secret` defaults to the dev-seed convention
        (`identity/app/seed.py`: `f"{local_name}-dev-secret"`), overridable
        for a principal seeded differently.
        """
        local_name = principal_urn.rsplit(":", 1)[-1]
        secret = client_secret if client_secret is not None else f"{local_name}-dev-secret"
        deadline = time.monotonic() + timeout
        last_status, last_body = None, None
        while time.monotonic() < deadline:
            last_status, last_body = self.request(
                "POST", f"{self.identity_url}/token", body={"principal_urn": principal_urn, "client_secret": secret}
            )
            if last_status == 200:
                return last_body["access_token"]
            time.sleep(1.5)
        raise TimeoutError(
            f"could not mint a token for {principal_urn} within {timeout}s "
            f"(last response: {last_status} {last_body})"
        )

    def sync_and_wait(
        self,
        *,
        connectivity_url: str,
        knowledge_url: str,
        token: str,
        dataset: str = "customers",
        timeout: float = 60.0,
    ) -> dict:
        """`POST /sync` returns as soon as the Iceberg commit lands, but
        Knowledge only catalogues it later, asynchronously, via its own
        outbox-relay/Kafka-consumer path — this closes that gap by
        polling `/catalog/datasets` for the matching (urn, snapshot_id)
        pair, not just index [0] (the bug this exact idiom used to have,
        fixed everywhere it appeared: the CI workflow's sync step and every
        per-file `..._synced` fixture built on this).
        """
        status, result = self.request(
            "POST", f"{connectivity_url}/sync", token=token, body={"dataset": dataset}
        )
        if status != 200:
            raise RuntimeError(f"POST /sync for {dataset!r} failed ({status}): {result}")

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            status, datasets = self.request("GET", f"{knowledge_url}/api/holon/catalog/datasets", token=token)
            if status != 200:
                raise RuntimeError(f"GET /catalog/datasets failed ({status}): {datasets}")
            match = next((d for d in datasets if d["urn"] == result["dataset_urn"]), None)
            if match is not None and match["snapshot_id"] == result["snapshot_id"]:
                return result
            time.sleep(1)
        raise TimeoutError(f"catalog did not converge to the new {dataset!r} snapshot within {timeout}s")
