#!/usr/bin/env python3
"""End-to-end walking skeleton demo: sync -> catalog -> dashboard.

Standard library only, so it runs with any Python 3.9+ and no extra
install (`holon_sdk` is stdlib-only too, same constraint). Assumes the
stack is already up (`make up`).
"""

from __future__ import annotations

import os
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from holon_sdk import HolonClient  # noqa: E402

# Same env-var-overridable-with-a-local-dev-fallback convention `cli/holon.py` uses.
IDENTITY = os.environ.get("HOLON_DEMO_IDENTITY_URL", "http://localhost:8001")
CONNECTIVITY = os.environ.get("HOLON_DEMO_CONNECTIVITY_URL", "http://localhost:8002")
KNOWLEDGE = os.environ.get("HOLON_DEMO_KNOWLEDGE_URL", "http://localhost:8003")
EXPERIENCE = os.environ.get("HOLON_DEMO_EXPERIENCE_URL", "http://localhost:8004")

client = HolonClient(identity_url=IDENTITY)


def _wait_healthy(name: str, url: str, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = client.request("GET", f"{url}/health")
            if status == 200:
                print(f"  {name} is up")
                return
        except (urllib.error.URLError, ConnectionError):
            pass
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
    token = client.token_for("hl:acme:global:user:jdoe")

    print("Running the PostgreSQL connector sync…")
    sync_result = client.sync_and_wait(connectivity_url=CONNECTIVITY, knowledge_url=KNOWLEDGE, token=token)
    print(f"  synced {sync_result['row_count']} rows -> {sync_result['dataset_version_urn']}")
    print(f"  catalogued: {sync_result['dataset_urn']} (snapshot {sync_result['snapshot_id']})")

    print("Reading Customer objects through the ontology API…")
    status, customers = client.request("GET", f"{KNOWLEDGE}/objects/Customer", token=token)
    assert status == 200, customers
    print(f"  {len(customers)} Customer instances resolved")

    print("\nChecking the PDP actually denies someone…")
    alice_token = client.token_for("hl:acme:global:user:alice")
    status, body = client.request("GET", f"{KNOWLEDGE}/objects/Customer", token=alice_token)
    if status == 200:
        print("  WARNING: alice was not denied — authorization may be misconfigured")
    else:
        print(f"  alice denied as expected ({status}): {body.get('detail')}")

    print(f"\nWalking skeleton complete. Open the dashboard: {EXPERIENCE}")


if __name__ == "__main__":
    main()
