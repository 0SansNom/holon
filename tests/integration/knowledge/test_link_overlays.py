"""P3 Link Types: generate join table + join/object_backed link overlays."""

from __future__ import annotations

import sys
import types
import uuid
from pathlib import Path

import pytest
from conftest import KNOWLEDGE, _request, _unique_name, ontology_url, holon_url

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_link_overlays():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    from app import link_overlays  # noqa: E402

    return link_overlays


def test_merge_pair_set_applies_add_and_delete() -> None:
    link_overlays = _import_link_overlays()
    merged = link_overlays.merge_pair_set(
        [(1, 10), (1, 11)],
        [
            {"source_id": "1", "target_id": "12", "op": "add"},
            {"source_id": "1", "target_id": "11", "op": "delete"},
        ],
    )
    assert merged == [("1", "10"), ("1", "12")]


def test_admin_can_generate_join_dataset(msmith_token: str) -> None:
    name = f"customer_order_{uuid.uuid4().hex[:8]}"
    status, body = _request(
        "POST",
        holon_url("/catalog/join-datasets"),
        token=msmith_token,
        body={"name": name, "source_column": "customer_id", "target_column": "order_id"},
    )
    assert status == 201, body
    assert body["dataset_urn"].endswith(f":dataset:join_{name}"), body
    assert body["source_column"] == "customer_id", body
    assert body["target_column"] == "order_id", body
    assert body["row_count"] == 0, body

    status, datasets = _request("GET", holon_url("/catalog/datasets"), token=msmith_token)
    assert status == 200, datasets
    assert any(d["urn"] == body["dataset_urn"] for d in datasets), datasets


def test_editor_cannot_generate_join_dataset(jdoe_token: str) -> None:
    status, body = _request(
        "POST",
        holon_url("/catalog/join-datasets"),
        token=jdoe_token,
        body={"name": "should_fail_bridge", "source_column": "a_id", "target_column": "b_id"},
    )
    assert status == 403, body


def test_join_dataset_link_write_and_unlink_via_overlay(msmith_token: str, jdoe_token: str) -> None:
    bridge_name = f"cust_ord_{uuid.uuid4().hex[:8]}"
    status, bridge = _request(
        "POST",
        holon_url("/catalog/join-datasets"),
        token=msmith_token,
        body={"name": bridge_name, "source_column": "customer_id", "target_column": "order_id"},
    )
    assert status == 201, bridge

    suffix = uuid.uuid4().hex[:8]
    rel_name = f"Customer.ordersViaGenJoin_{suffix}"
    fwd = f"ordersViaGenJoin_{suffix}"
    rev = f"customersViaGenJoin_{suffix}"
    status, created = _request(
        "POST",
        ontology_url("/linkTypes"),
        token=msmith_token,
        body={
            "name": rel_name,
            "source_object_type": "Customer",
            "target_object_type": "Order",
            "target_property": rev,
            "cardinality": "many_to_many",
            "storage_kind": "join_dataset",
            "join_dataset_urn": bridge["dataset_urn"],
            "join_source_column": "customer_id",
            "join_target_column": "order_id",
            "source_api_name": fwd,
            "target_api_name": rev,
        },
    )
    assert status == 201, created

    status, linked = _request(
        "PUT",
        ontology_url(f"/objects/Customer/1/links/{fwd}"),
        token=jdoe_token,
        body={"target_id": 1},
    )
    assert status == 200, linked
    assert any(int(item["id"]) == 1 for item in linked["data"]), linked

    status, again = _request(
        "GET", ontology_url(f"/objects/Customer/1/links/{fwd}"), token=jdoe_token
    )
    assert status == 200, again
    assert any(int(item["id"]) == 1 for item in again["data"]), again

    status, unlinked = _request(
        "DELETE",
        ontology_url(f"/objects/Customer/1/links/{fwd}?target_id=1"),
        token=jdoe_token,
    )
    assert status == 200, unlinked
    assert unlinked["data"] == [], unlinked


def test_object_backed_link_write_via_overlay(msmith_token: str, jdoe_token: str) -> None:
    suffix = uuid.uuid4().hex[:8]
    rel_name = f"Customer.ordersViaMidOv_{suffix}"
    fwd = f"ordersViaMidOv_{suffix}"
    rev = f"customersViaMidOv_{suffix}"
    status, created = _request(
        "POST",
        ontology_url("/linkTypes"),
        token=msmith_token,
        body={
            "name": rel_name,
            "source_object_type": "Customer",
            "target_object_type": "Order",
            "target_property": rev,
            "cardinality": "many_to_many",
            "storage_kind": "object_backed",
            "mid_object_type": "SupportTicket",
            "mid_source_property": "customerId",
            "mid_target_property": "id",
            "source_api_name": fwd,
            "target_api_name": rev,
        },
    )
    assert status == 201, created

    # Use an Order id unlikely to equal a SupportTicket.id projection for customer 1.
    # Overlay injects the pair even when no mid Iceberg row exists.
    target_order = 2
    status, linked = _request(
        "PUT",
        ontology_url(f"/objects/Customer/1/links/{fwd}"),
        token=jdoe_token,
        body={"target_id": target_order},
    )
    assert status == 200, linked
    assert any(int(item["id"]) == target_order for item in linked["data"]), linked

    status, wb = _request(
        "GET", ontology_url(f"/linkTypes/{rel_name}/writeback-status"), token=jdoe_token
    )
    assert status == 200, wb
    assert wb["overlay_count"] >= 1, wb
    assert wb["has_writeback_risk"] is True, wb
