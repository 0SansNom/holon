"""Bi-temporal instance history, scoped to **transaction-time** ("what did the
system believe, and when did it record believing it").

Mutates a customer's email directly in `source_erp` mid-test (same
technique as `test_reproducibility.py`) and proves `?as_of=<a timestamp
before the mutation>` still returns the *old* email while a plain read
returns the *new* one — a genuine round-trip through real history, not
just a schema check. Requires the stack running (`make up`).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import asyncpg
import pytest

IDENTITY = "http://localhost:8001"
CONNECTIVITY = "http://localhost:8002"
KNOWLEDGE = "http://localhost:8003"

TENANT_ID = "acme"
# Password read from the environment, not hardcoded — CI generates its
# own .env with a different POSTGRES_PASSWORD than a dev's local one
# (see .github/workflows/tests.yml), so a hardcoded value here only ever
# worked by coincidence locally. Default matches .env.example's dev
# convenience value for a plain `pytest tests/` run against `make up`.
SOURCE_ERP_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/source_erp"

# Nordic Freight — not used by any assertion in other test modules that
# mutate customer email (test_reproducibility.py uses customer 4).
CUSTOMER_ID = 2
MUTATED_EMAIL = "p9-bitemporal-test@example.invalid"


def _request(method: str, url: str, *, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _token_for(principal_urn: str) -> str:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        local_name = principal_urn.rsplit(":", 1)[-1]
        status, body = _request(
            "POST",
            f"{IDENTITY}/token",
            body={"principal_urn": principal_urn, "client_secret": f"{local_name}-dev-secret"},
        )
        if status == 200:
            return body["access_token"]
        time.sleep(1.5)
    pytest.fail(f"could not mint a token for {principal_urn}")


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    return _token_for(f"hl:{TENANT_ID}:global:user:jdoe")


async def _get_email(customer_id: int) -> str:
    conn = await asyncpg.connect(SOURCE_ERP_URL)
    try:
        return await conn.fetchval("SELECT email FROM customers WHERE id = $1", customer_id)
    finally:
        await conn.close()


async def _set_email(customer_id: int, email: str) -> None:
    conn = await asyncpg.connect(SOURCE_ERP_URL)
    try:
        await conn.execute("UPDATE customers SET email = $1 WHERE id = $2", email, customer_id)
    finally:
        await conn.close()


def _sync_and_wait(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "customers"})
    assert status == 200, result

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        status, datasets = _request("GET", f"{KNOWLEDGE}/catalog/datasets", token=jdoe_token)
        assert status == 200
        customers = next((d for d in datasets if d["urn"] == result["dataset_urn"]), None)
        if customers and customers["snapshot_id"] == result["snapshot_id"]:
            return result
        time.sleep(1)
    pytest.fail("catalog did not converge to the new customers snapshot in time")


def test_as_of_read_returns_the_state_that_was_true_at_that_time(jdoe_token: str) -> None:
    original_email = asyncio.run(_get_email(CUSTOMER_ID))

    try:
        # 1. Establish a known-good historical point: sync now, confirm the
        # live read matches, and record the timestamp right after.
        _sync_and_wait(jdoe_token)
        status, before = _request("GET", f"{KNOWLEDGE}/objects/Customer/{CUSTOMER_ID}", token=jdoe_token)
        assert status == 200, before
        assert before["email"] == original_email, before
        as_of_before = before["materializedAt"]

        # 2. Mutate the source and re-sync — the live read now differs.
        asyncio.run(_set_email(CUSTOMER_ID, MUTATED_EMAIL))
        _sync_and_wait(jdoe_token)

        status, after = _request("GET", f"{KNOWLEDGE}/objects/Customer/{CUSTOMER_ID}", token=jdoe_token)
        assert status == 200, after
        assert after["email"] == MUTATED_EMAIL, after

        # 3. A historical read pinned to the pre-mutation timestamp must
        # still show the *old* email — real bi-temporal (transaction-time)
        # round-trip, not just "the field exists."
        status, historical = _request(
            "GET",
            f"{KNOWLEDGE}/objects/Customer/{CUSTOMER_ID}?as_of={urllib.parse.quote(as_of_before)}",
            token=jdoe_token,
        )
        assert status == 200, historical
        assert historical["email"] == original_email, (historical, original_email)
        assert historical["asOf"], historical
    finally:
        asyncio.run(_set_email(CUSTOMER_ID, original_email))
        _sync_and_wait(jdoe_token)


def test_as_of_far_in_the_past_has_no_history(jdoe_token: str) -> None:
    status, body = _request(
        "GET", f"{KNOWLEDGE}/objects/Customer/{CUSTOMER_ID}?as_of=2000-01-01T00:00:00Z", token=jdoe_token
    )
    assert status == 404, body
