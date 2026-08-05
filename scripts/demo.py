#!/usr/bin/env python3
"""End-to-end walking skeleton demo: sync -> catalog -> dashboard.

Standard library only, so it runs with any Python 3.9+ and no extra
install. Assumes the stack is already up (`make up`).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

IDENTITY = "http://localhost:8001"
CONNECTIVITY = "http://localhost:8002"
KNOWLEDGE = "http://localhost:8003"
EXPERIENCE = "http://localhost:8004"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def _wait_healthy(name: str, url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _request("GET", f"{url}/health")
            print(f"  {name} is up")
            return
        except (urllib.error.URLError, ConnectionError):
            time.sleep(1.5)
    raise TimeoutError(f"{name} did not become healthy within {timeout}s")


def main() -> None:
    print("Waiting for services…")
    for name, url in [
        ("identity", IDENTITY),
        ("connectivity", CONNECTIVITY),
        ("knowledge", KNOWLEDGE),
        ("experience", EXPERIENCE),
    ]:
        _wait_healthy(name, url)

    print("\nSigning in as Jane Doe…")
    token = _request(
        "POST",
        f"{IDENTITY}/token",
        body={"principal_urn": "hl:acme:global:user:jdoe", "client_secret": "jdoe-dev-secret"},
    )["access_token"]

    print("Running the PostgreSQL connector sync…")
    sync_result = _request("POST", f"{CONNECTIVITY}/sync", token=token)
    print(f"  synced {sync_result['row_count']} rows -> {sync_result['dataset_version_urn']}")

    print("Waiting for the catalog to converge…")
    deadline = time.monotonic() + 30
    datasets: list = []
    while time.monotonic() < deadline:
        datasets = _request("GET", f"{KNOWLEDGE}/catalog/datasets", token=token)
        if datasets and datasets[0]["snapshot_id"] == sync_result["snapshot_id"]:
            break
        time.sleep(1)
    else:
        raise TimeoutError("catalog did not converge in time")
    print(f"  catalogued: {datasets[0]['urn']} (snapshot {datasets[0]['snapshot_id']})")

    print("Reading Customer objects through the ontology API…")
    customers = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=token)
    print(f"  {len(customers)} Customer instances resolved")

    print("\nChecking the PDP actually denies someone…")
    alice_token = _request(
        "POST",
        f"{IDENTITY}/token",
        body={"principal_urn": "hl:acme:global:user:alice", "client_secret": "alice-dev-secret"},
    )["access_token"]
    try:
        _request("GET", f"{KNOWLEDGE}/objects/Customer", token=alice_token)
        print("  WARNING: alice was not denied — authorization may be misconfigured")
    except urllib.error.HTTPError as exc:
        reason = json.loads(exc.read())
        print(f"  alice denied as expected ({exc.code}): {reason.get('detail')}")

    print(f"\nWalking skeleton complete. Open the dashboard: {EXPERIENCE}")


if __name__ == "__main__":
    main()
