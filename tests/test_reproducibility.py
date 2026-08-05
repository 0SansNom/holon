"""Reproducibility test — replaying a run with the same plan and URNs
produces identical results even if underlying data changes.

White-box for the mutation/revert (direct `asyncpg` against `source_erp`,
same treatment as `test_projection_rebuild.py`'s direct Postgres access),
black-box HTTP for everything else. Requires the stack running (`make up`).
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
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

# Kappa Foundries — not used by any assertion in other test modules, so
# mutating and reverting its email here is safe self-contained state.
CUSTOMER_ID = 4
MUTATED_EMAIL = "r1-14-reproducibility-test@example.invalid"


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


def test_replaying_a_frozen_plan_reproduces_the_original_result_despite_a_later_mutation(jdoe_token: str) -> None:
    original_email = asyncio.run(_get_email(CUSTOMER_ID))

    try:
        # 1. A fresh execution, pinned to whatever the customer's email is now.
        _sync_and_wait(jdoe_token)
        status, before = _request(
            "POST",
            f"{KNOWLEDGE}/execute",
            token=jdoe_token,
            body={"object_type": "Customer", "filter_property": "id", "filter_value": str(CUSTOMER_ID)},
        )
        assert status == 200, before
        assert before["results"][0]["email"] == original_email, before
        plan_hash_a = before["planHash"]

        # 2. Mutate the source directly, then re-sync so the *live* path
        # picks it up — proving the mutation is real, not a no-op.
        asyncio.run(_set_email(CUSTOMER_ID, MUTATED_EMAIL))
        _sync_and_wait(jdoe_token)

        status, after = _request(
            "POST",
            f"{KNOWLEDGE}/execute",
            token=jdoe_token,
            body={"object_type": "Customer", "filter_property": "id", "filter_value": str(CUSTOMER_ID)},
        )
        assert status == 200, after
        assert after["planHash"] != plan_hash_a, (before, after)  # a new DatasetVersion -> a new plan hash
        assert after["results"][0]["email"] == MUTATED_EMAIL, after

        # 3. Replaying the *original* plan must still reproduce the
        # *original* result — bit-identical, despite the live data having
        # since changed.
        status, replayed = _request("POST", f"{KNOWLEDGE}/execute/{plan_hash_a}/replay", token=jdoe_token)
        assert status == 200, replayed
        assert replayed["reproducible"] is True, replayed
        assert replayed["result"] == before["results"], replayed
        assert replayed["result"][0]["email"] == original_email, replayed
    finally:
        asyncio.run(_set_email(CUSTOMER_ID, original_email))
        _sync_and_wait(jdoe_token)


def test_replay_of_unknown_plan_hash_is_rejected(jdoe_token: str) -> None:
    status, body = _request("POST", f"{KNOWLEDGE}/execute/doesnotexist/replay", token=jdoe_token)
    assert status == 404, body
    assert "no execution_run found" in body["detail"], body
