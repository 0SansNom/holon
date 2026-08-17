"""RelationType Foundry-side metadata + delete (P0) + project ACL (P1)."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from conftest import KNOWLEDGE, _request, _unique_name, ontology_url, holon_url

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_relation_helpers():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules["app.ontology"] = ontology_pkg
    from app.ontology.relation_types import (  # noqa: E402
        _normalize_side_metadata,
        _normalize_type_classes,
    )

    return _normalize_side_metadata, _normalize_type_classes


_normalize_side_metadata, _normalize_type_classes = _import_relation_helpers()


def test_side_metadata_requires_api_name_and_valid_visibility() -> None:
    with pytest.raises(ValueError, match="source_api_name"):
        _normalize_side_metadata(
            display_name="",
            plural_display_name="",
            api_name="  ",
            visibility="normal",
            side="source",
        )
    with pytest.raises(ValueError, match="visibility"):
        _normalize_side_metadata(
            display_name="Customer",
            plural_display_name="Customers",
            api_name="customer",
            visibility="loud",
            side="source",
        )
    assert _normalize_side_metadata(
        display_name="Customer",
        plural_display_name="Customers",
        api_name="customer",
        visibility="hidden",
        side="source",
    ) == ("Customer", "Customers", "customer", "hidden")


def test_type_classes_must_be_strings() -> None:
    with pytest.raises(ValueError, match="type_classes"):
        _normalize_type_classes([1])  # type: ignore[list-item]
    assert _normalize_type_classes(["core", "nav"]) == ["core", "nav"]


def test_seeded_relation_exposes_foundry_side_metadata(jdoe_token: str) -> None:
    status, relation = _request("GET", ontology_url("/linkTypes/Order.customer"), token=jdoe_token)
    assert status == 200, relation
    assert relation.get("source_api_name") in ("customer", ""), relation
    assert relation.get("target_api_name") in ("orders", ""), relation
    assert relation.get("lifecycle_status") in ("active", "experimental"), relation
    assert isinstance(relation.get("type_classes", []), list), relation


def test_admin_can_create_with_side_metadata_and_delete(msmith_token: str, jdoe_token: str) -> None:
    name = _unique_name("Order.metaCustomer")
    status, created = _request(
        "POST",
        ontology_url("/linkTypes"),
        token=msmith_token,
        body={
            "name": name,
            "source_object_type": "Order",
            "target_object_type": "Customer",
            "source_property": "customerId",
            "target_property": "ordersViaMeta",
            "cardinality": "many_to_one",
            "source_display_name": "Customer",
            "source_plural_display_name": "Customers",
            "source_api_name": "metaCustomer",
            "source_visibility": "prominent",
            "target_display_name": "Order",
            "target_plural_display_name": "Orders",
            "target_api_name": "metaOrders",
            "target_visibility": "normal",
            "lifecycle_status": "experimental",
            "type_classes": ["core"],
        },
    )
    assert status in (201, 409), created
    if status == 201:
        assert created["source_api_name"] == "metaCustomer", created
        assert created["target_api_name"] == "metaOrders", created
        assert created["source_visibility"] == "prominent", created
        assert created["type_classes"] == ["core"], created

    status, updated = _request(
        "PUT",
        ontology_url(f"/linkTypes/{name}"),
        token=msmith_token,
        body={
            "source_display_name": "Linked customer",
            "storage_kind": "foreign_key",
            "lifecycle_status": "deprecated",
            "deprecation_reason": "superseded by metaCustomer v2",
            "deprecation_deadline": "2027-01-01",
        },
    )
    assert status == 200, updated
    assert updated["source_display_name"] == "Linked customer", updated
    assert updated["lifecycle_status"] == "deprecated", updated

    status, perms = _request("GET", ontology_url(f"/linkTypes/{name}/permissions"), token=msmith_token)
    assert status == 200, perms
    assert perms["permissions"]["approve"] is True, perms

    status, deleted = _request("DELETE", ontology_url(f"/linkTypes/{name}"), token=msmith_token)
    assert status == 200, deleted
    assert deleted.get("deleted") is True, deleted

    status, missing = _request("GET", ontology_url(f"/linkTypes/{name}"), token=jdoe_token)
    assert status == 404, missing


def test_cannot_delete_active_relation_type(msmith_token: str) -> None:
    status, body = _request("DELETE", ontology_url("/linkTypes/Order.customer"), token=msmith_token)
    assert status == 400, body
    assert "active" in body["detail"], body


def test_editor_cannot_delete_relation_type(jdoe_token: str) -> None:
    status, body = _request("DELETE", ontology_url("/linkTypes/Order.customer"), token=jdoe_token)
    assert status == 403, body


def test_join_dataset_execute_requires_synced_bridge(msmith_token: str, jdoe_token: str) -> None:
    name = _unique_name("Customer.productsViaJoinExec")
    status, created = _request(
        "POST",
        ontology_url("/linkTypes"),
        token=msmith_token,
        body={
            "name": name,
            "source_object_type": "Customer",
            "target_object_type": "Order",
            "source_property": "",
            "target_property": "customersViaJoinExec",
            "cardinality": "many_to_many",
            "storage_kind": "join_dataset",
            "join_dataset_urn": "hl:acme:main:dataset:customer_order_bridge_exec",
            "join_source_column": "customer_id",
            "join_target_column": "order_id",
            "lifecycle_status": "experimental",
        },
    )
    assert status in (201, 409), created

    status, body = _request(
        "POST",
        holon_url("/execute"),
        token=jdoe_token,
        body={"object_type": "Customer", "operation": "join", "relation_name": name},
    )
    assert status == 400, body
    assert "bridge" in body["detail"].lower() or "synced" in body["detail"].lower(), body
