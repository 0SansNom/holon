"""Reproducibility test — replaying a run with the same plan and URNs
produces identical results even if underlying data changes.

White-box for the mutation/revert (direct `asyncpg` against `source_erp`,
same treatment as `test_projection_rebuild.py`'s direct Postgres access),
black-box HTTP for everything else. Requires the stack running (`make up`).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import asyncpg
import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, TENANT_ID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libs"))

from holon_sdk import HolonClient  # noqa: E402


SOURCE_ERP_URL = f"postgresql://holon:{os.environ.get('POSTGRES_PASSWORD', 'holon12345')}@localhost:5432/source_erp"

# Kappa Foundries — not used by any assertion in other test modules, so
# mutating and reverting its email here is safe self-contained state.
CUSTOMER_ID = 4
MUTATED_EMAIL = "r1-14-reproducibility-test@example.invalid"

client = HolonClient(identity_url=IDENTITY)
_request = client.request


@pytest.fixture(scope="session")
def jdoe_token() -> str:
    try:
        return client.token_for(f"hl:{TENANT_ID}:global:user:jdoe")
    except TimeoutError as exc:
        pytest.fail(str(exc))


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
    try:
        return client.sync_and_wait(connectivity_url=CONNECTIVITY, knowledge_url=KNOWLEDGE, token=jdoe_token)
    except (RuntimeError, TimeoutError) as exc:
        pytest.fail(str(exc))


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
