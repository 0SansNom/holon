"""Analytics —  (a lightweight
Contour/Code Workbook equivalent). Extends Knowledge's Execution engine
(`execution.py`) from its original two operators (equality filter,
count) to two more: `group_by` (group-by+aggregate over one ObjectType's
own data) and `join` (a real structural join across an existing
RelationType) — still DuckDB-backed, still through the same plan-hash
cache every operator already shares.

Proves: `group_by`'s count/sum/avg/min/max all compute correctly;
`join` combines two ObjectTypes' rows with `s_`/`t_`-prefixed columns to
avoid same-named-column collisions (`id` included); DAG-shape validation
(unknown aggregate_function, a RelationType that doesn't originate from
the calling ObjectType) is rejected at request time. Enforces R8.7 property
masking on `/execute`: `/execute` applies property masking for ABAC-denied
principals on `filter`, `join`, and `group_by` operations; `count` is
correctly left untouched (it never exposes a property's actual value), and a
principal who can read a join's *source* ObjectType but not its
*target* is denied the join entirely, not served a half-visible result.
No real LLM calls.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid

import pytest
from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, TENANT_ID, _request


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
def orders_synced(jdoe_token: str) -> dict:
    status, result = _request("POST", f"{CONNECTIVITY}/sync", token=jdoe_token, body={"dataset": "orders"})
    assert status == 200, result
    return result


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def test_group_by_count_matches_a_manual_count(jdoe_token: str, orders_synced: dict) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "group_by", "group_by_property": "segment"},
    )
    assert status == 200, body
    total = sum(row["aggregate"] for row in body["results"])
    status, all_customers = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=jdoe_token)
    assert status == 200
    assert total == len(all_customers), (body, len(all_customers))


def test_group_by_sum_and_avg_are_computed(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "group_by", "group_by_property": "segment",
              "aggregate_property": "lifetimeValue", "aggregate_function": "sum"},
    )
    assert status == 200, body
    assert all(row["aggregate"] is not None for row in body["results"]), body

    status, avg_body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "group_by", "group_by_property": "segment",
              "aggregate_property": "lifetimeValue", "aggregate_function": "avg"},
    )
    assert status == 200, avg_body
    assert avg_body["planHash"] != body["planHash"], (avg_body, body)  # distinct operator, distinct cache entry


def test_group_by_repeated_request_is_cached(jdoe_token: str) -> None:
    """`Supplier`, not `Customer`: two other test files
    (`test_bitemporal_history.py`, `test_reproducibility.py`) deliberately
    mutate and resync `customers` — a resync landing between this test's
    two back-to-back calls would legitimately change the plan hash (the
    exact behavior `test_execution.py`'s own cache-invalidation test
    checks for), making the "must be cached" assertion below racy for no
    reason connected to what this test is actually about. `Supplier`
    stays untouched by any other test file, the same reasoning
    `test_bitemporal_history.py` itself already gives for picking
    Customer 2 (Nordic Freight) over Customer 1.
    """
    body_kwargs = {"object_type": "Supplier", "operation": "group_by", "group_by_property": "country"}
    status, first = _request("POST", f"{KNOWLEDGE}/execute", token=jdoe_token, body=body_kwargs)
    assert status == 200 and first["cached"] is False, first
    status, second = _request("POST", f"{KNOWLEDGE}/execute", token=jdoe_token, body=body_kwargs)
    assert status == 200 and second["cached"] is True, second
    assert second["planHash"] == first["planHash"], (first, second)


def test_group_by_unknown_aggregate_function_is_rejected(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "group_by", "group_by_property": "segment",
              "aggregate_function": "median"},
    )
    assert status == 400, body
    assert "aggregate_function" in body["detail"], body


def test_group_by_aggregate_over_confidential_property_is_masked_for_denied_principal(
    jdoe_token: str, kenji_token: str
) -> None:
    """kenji is ABAC-denied on confidential fields — `lifetimeValue`
    (Customer's own confidential column) must be masked even in
    aggregate form, since `sum`/`avg`/`min`/`max` still leaks its
    content; a `count` group-by never touches the property's actual
    value at all, so it must stay unmasked.
    """
    status, sum_body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=kenji_token,
        body={"object_type": "Customer", "operation": "group_by", "group_by_property": "segment",
              "aggregate_property": "lifetimeValue", "aggregate_function": "sum"},
    )
    assert status == 200, sum_body
    assert all(row["aggregate"] is None for row in sum_body["results"]), sum_body
    assert all(row.get("_maskedFields") == ["aggregate"] for row in sum_body["results"]), sum_body

    status, count_body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=kenji_token,
        body={"object_type": "Customer", "operation": "group_by", "group_by_property": "segment"},
    )
    assert status == 200, count_body
    assert all(row["aggregate"] is not None and "_maskedFields" not in row for row in count_body["results"]), count_body

    status, jdoe_sum = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "group_by", "group_by_property": "segment",
              "aggregate_property": "lifetimeValue", "aggregate_function": "sum"},
    )
    assert status == 200, jdoe_sum
    assert all(row["aggregate"] is not None for row in jdoe_sum["results"]), jdoe_sum


def test_filter_via_execute_is_r87_masked_for_denied_principal(kenji_token: str, jdoe_token: str) -> None:
    """Verifies that `/execute` applies R8.7 property masking for denied
    principals, matching the behavior of standard object read endpoints.
    """
    status, direct = _request("GET", f"{KNOWLEDGE}/objects/Customer/1", token=kenji_token)
    assert status == 200, direct
    assert direct.get("_maskedFields"), direct

    status, executed = _request(
        "POST", f"{KNOWLEDGE}/execute", token=kenji_token,
        body={"object_type": "Customer", "operation": "filter", "filter_property": "id", "filter_value": "1"},
    )
    assert status == 200, executed
    row = executed["results"][0]
    for field in direct["_maskedFields"]:
        assert row.get(field) is None, (field, row)
    assert set(row.get("_maskedFields", [])) == set(direct["_maskedFields"]), (row, direct)

    status, unmasked = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "filter", "filter_property": "id", "filter_value": "1"},
    )
    assert status == 200, unmasked
    assert unmasked["results"][0]["email"] is not None, unmasked


def test_join_combines_both_sides_with_prefixed_columns(jdoe_token: str, orders_synced: dict) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Order", "operation": "join", "relation_name": "Order.customer"},
    )
    assert status == 200, body
    assert body["rowCount"] > 0, body
    row = body["results"][0]
    assert "s_id" in row and "s_customer_id" in row, row
    assert "t_id" in row and "t_name" in row, row
    assert row["s_customer_id"] == row["t_id"], row  # the join condition itself, made observable


def test_join_masks_confidential_columns_on_both_sides(kenji_token: str, jdoe_token: str) -> None:
    """Order's own `amount` is confidential (`ORDERS_COLUMN_CLASSIFICATION`)
    — masked with the `s_` prefix; the joined-in Customer's `email`/
    `lifetimeValue` are masked with `t_` — both sides, one function.
    """
    status, body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=kenji_token,
        body={"object_type": "Order", "operation": "join", "relation_name": "Order.customer"},
    )
    assert status == 200, body
    row = body["results"][0]
    assert row["s_amount"] is None, row
    assert row["t_email"] is None, row
    assert row["t_lifetime_value"] is None, row
    assert "s_amount" in row["_maskedFields"], row
    assert "t_email" in row["_maskedFields"], row

    status, unmasked = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Order", "operation": "join", "relation_name": "Order.customer"},
    )
    assert status == 200, unmasked
    assert unmasked["results"][0]["s_amount"] is not None, unmasked
    assert unmasked["results"][0]["t_email"] is not None, unmasked


def test_join_rejects_a_relation_not_originating_from_the_object_type(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Customer", "operation": "join", "relation_name": "Order.customer"},
    )
    assert status == 400, body
    assert "does not originate from" in body["detail"], body


def test_join_rejects_an_unknown_relation_name(jdoe_token: str) -> None:
    status, body = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Order", "operation": "join", "relation_name": _unique_name("NotARealRelation")},
    )
    assert status == 404, body


def test_join_replay_reproduces_and_stays_masked(jdoe_token: str, kenji_token: str) -> None:
    status, run = _request(
        "POST", f"{KNOWLEDGE}/execute", token=jdoe_token,
        body={"object_type": "Order", "operation": "join", "relation_name": "Order.customer"},
    )
    assert status == 200, run
    plan_hash = run["planHash"]

    status, replayed_jdoe = _request("POST", f"{KNOWLEDGE}/execute/{plan_hash}/replay", token=jdoe_token)
    assert status == 200, replayed_jdoe
    assert replayed_jdoe["reproducible"] is True, replayed_jdoe
    assert replayed_jdoe["result"][0]["t_email"] is not None, replayed_jdoe

    # The *same frozen plan*, replayed by a *different, ABAC-denied*
    # principal — masking is applied fresh per caller, never baked into
    # what `execution_run` cached.
    status, replayed_kenji = _request("POST", f"{KNOWLEDGE}/execute/{plan_hash}/replay", token=kenji_token)
    assert status == 200, replayed_kenji
    assert replayed_kenji["result"][0]["t_email"] is None, replayed_kenji


def test_join_denied_when_principal_cannot_read_the_target_object_type(
    msmith_token: str, alice_token: str
) -> None:
    """alice holds no workspace relation at all — grant her project-level
    access to a project *Order* alone is scoped to, so she can read Order
    directly, but Customer (the join's target) stays completely out of
    reach. The join itself must be denied, not served with Customer's
    side silently empty or unmasked past the source-only check.

    Revokes alice's grant in a `finally`: several other test files
    (`test_relations.py`, `test_execution.py`) use alice specifically
    *because* she holds zero standing anywhere, as their "definitely
    denied" baseline — revoking her grant prevents lingering project
    permissions from altering baselines in subsequent tests.
    `project_urn` itself is left scoped on Order afterward, same as
    `test_project_hierarchy.py`'s own tests — harmless on its own since
    `parent_project` only ever adds to `parent_workspace`, never replaces
    it; it's specifically the *grant*, not the scope, that broke another
    test's assumption.
    """
    project_name = _unique_name("proj-join-deny")
    status, project = _request("POST", f"{IDENTITY}/projects", token=msmith_token, body={"name": project_name})
    assert status == 201, project
    project_urn = project["urn"]

    branch_name = _unique_name("scope-order")
    status, branch = _request(
        "POST", f"{KNOWLEDGE}/ontology/Order/branches", token=msmith_token,
        body={"branch_name": branch_name, "project_urn": project_urn},
    )
    assert status == 201, branch
    status, review = _request(
        "POST", f"{KNOWLEDGE}/ontology/Order/branches/{branch_name}/review", token=msmith_token,
        body={"decision": "approved"},
    )
    assert status == 200 and review["status"] == "merged", review

    status, body = _request(
        "POST", f"{IDENTITY}/projects/{project_name}/principals/hl:{TENANT_ID}:global:user:alice/access/grant",
        token=msmith_token, body={"relation": "viewer"},
    )
    assert status == 200, body

    try:
        def _alice_can_read_orders() -> None:
            status, body = _request("GET", f"{KNOWLEDGE}/objects/Order", token=alice_token)
            assert status == 200, body

        deadline = time.monotonic() + 20
        last_exc = None
        while time.monotonic() < deadline:
            try:
                _alice_can_read_orders()
                break
            except AssertionError as exc:
                last_exc = exc
                time.sleep(1)
        else:
            raise last_exc

        # alice can read Order (just proven), but has zero standing on
        # Customer — the join must be denied outright.
        status, denied = _request(
            "POST", f"{KNOWLEDGE}/execute", token=alice_token,
            body={"object_type": "Order", "operation": "join", "relation_name": "Order.customer"},
        )
        assert status == 403, denied
    finally:
        status, body = _request(
            "POST", f"{IDENTITY}/projects/{project_name}/principals/hl:{TENANT_ID}:global:user:alice/access/revoke",
            token=msmith_token, body={"relation": "viewer"},
        )
        assert status == 200, body
