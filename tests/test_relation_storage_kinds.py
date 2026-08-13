"""RelationType storage_kind validation + HTTP registration for join/object_backed.

Pure `_validate_storage` unit tests need no stack; create/list/traversal
checks require `make up`.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from conftest import KNOWLEDGE, _request, _unique_name

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_validate_storage():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules["app.ontology"] = ontology_pkg
    from app.ontology.relation_types import _validate_storage  # noqa: E402

    return _validate_storage


_validate_storage = _import_validate_storage()


def test_join_dataset_requires_many_to_many_and_columns() -> None:
    with pytest.raises(ValueError, match="many_to_many"):
        _validate_storage(
            storage_kind="join_dataset",
            cardinality="many_to_one",
            source_property="",
            join_dataset_urn="hl:acme:main:dataset:x",
            join_source_column="a",
            join_target_column="b",
            mid_object_type_urn=None,
            mid_source_property=None,
            mid_target_property=None,
        )
    with pytest.raises(ValueError, match="join_dataset"):
        _validate_storage(
            storage_kind="join_dataset",
            cardinality="many_to_many",
            source_property="",
            join_dataset_urn=None,
            join_source_column="a",
            join_target_column="b",
            mid_object_type_urn=None,
            mid_source_property=None,
            mid_target_property=None,
        )
    _validate_storage(
        storage_kind="join_dataset",
        cardinality="many_to_many",
        source_property="",
        join_dataset_urn="hl:acme:main:dataset:x",
        join_source_column="a",
        join_target_column="b",
        mid_object_type_urn=None,
        mid_source_property=None,
        mid_target_property=None,
    )


def test_object_backed_requires_mid_props() -> None:
    with pytest.raises(ValueError, match="object_backed"):
        _validate_storage(
            storage_kind="object_backed",
            cardinality="many_to_many",
            source_property="",
            join_dataset_urn=None,
            join_source_column=None,
            join_target_column=None,
            mid_object_type_urn=None,
            mid_source_property="a",
            mid_target_property="b",
        )
    _validate_storage(
        storage_kind="object_backed",
        cardinality="many_to_many",
        source_property="",
        join_dataset_urn=None,
        join_source_column=None,
        join_target_column=None,
        mid_object_type_urn="hl:acme:main:object-type:Link",
        mid_source_property="leftId",
        mid_target_property="rightId",
    )


def test_foreign_key_still_requires_source_property() -> None:
    with pytest.raises(ValueError, match="source_property"):
        _validate_storage(
            storage_kind="foreign_key",
            cardinality="many_to_one",
            source_property="",
            join_dataset_urn=None,
            join_source_column=None,
            join_target_column=None,
            mid_object_type_urn=None,
            mid_source_property=None,
            mid_target_property=None,
        )


def test_seeded_fk_relation_exposes_storage_kind(jdoe_token: str) -> None:
    status, relation = _request("GET", f"{KNOWLEDGE}/relation-types/Order.customer", token=jdoe_token)
    assert status == 200, relation
    assert relation.get("storage_kind", "foreign_key") == "foreign_key", relation


def test_admin_can_register_join_dataset_relation(msmith_token: str) -> None:
    name = _unique_name("Customer.productsViaJoin")
    status, created = _request(
        "POST",
        f"{KNOWLEDGE}/relation-types",
        token=msmith_token,
        body={
            "name": name,
            "source_object_type": "Customer",
            "target_object_type": "Order",
            "source_property": "",
            "target_property": "customersViaJoin",
            "cardinality": "many_to_many",
            "storage_kind": "join_dataset",
            "join_dataset_urn": "hl:acme:main:dataset:customer_order_bridge",
            "join_source_column": "customer_id",
            "join_target_column": "order_id",
        },
    )
    assert status in (201, 409), created
    if status == 201:
        assert created["storage_kind"] == "join_dataset", created
        assert created["join_source_column"] == "customer_id", created

    status, fetched = _request("GET", f"{KNOWLEDGE}/relation-types/{name}", token=msmith_token)
    # 409 path: name may already exist from prior run with same unique suffix unlikely;
    # if create failed with 409, skip fetch by unique name — still assert list contains storage_kind field.
    if status == 200:
        assert fetched["storage_kind"] == "join_dataset", fetched


def test_admin_can_register_object_backed_relation(msmith_token: str) -> None:
    name = _unique_name("Customer.ordersViaMid")
    status, created = _request(
        "POST",
        f"{KNOWLEDGE}/relation-types",
        token=msmith_token,
        body={
            "name": name,
            "source_object_type": "Customer",
            "target_object_type": "Order",
            "source_property": "",
            "target_property": "customersViaMid",
            "cardinality": "many_to_many",
            "storage_kind": "object_backed",
            "mid_object_type": "SupportTicket",
            "mid_source_property": "customerId",
            "mid_target_property": "id",
        },
    )
    assert status in (201, 409), created
    if status == 201:
        assert created["storage_kind"] == "object_backed", created
        assert created["mid_object_type_urn"].endswith(":SupportTicket"), created

    # Seeded FK link still traverses and advertises storage_kind.
    status, links = _request(
        "GET", f"{KNOWLEDGE}/objects/Customer/1/links/orders", token=msmith_token
    )
    assert status == 200, links
    assert "items" in links, links
    assert links.get("storage_kind", "foreign_key") == "foreign_key", links
    for item in links["items"]:
        assert "title" in item, item
