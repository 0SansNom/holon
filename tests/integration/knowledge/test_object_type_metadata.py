"""Tests for Object Type Metadata."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest
from conftest import CONNECTIVITY, KNOWLEDGE, _request, _unique_name, ontology_url, holon_url

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_object_types():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    # Avoid executing app.ontology.__init__ (pulls the whole registry).
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules.setdefault("app", app)
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules["app.ontology"] = ontology_pkg
    from app.ontology.object_types import title_of, validate_ot_metadata  # noqa: E402

    return title_of, validate_ot_metadata


title_of, validate_ot_metadata = _import_object_types()


def test_title_of_prefers_title_key_then_primary_key() -> None:
    instance = {"id": 7, "name": "Acme", "code": "A1"}
    object_type = {
        "title_key": "name",
        "primary_key": "id",
        "property_mapping": {"id": "id", "name": "name", "code": "code"},
    }
    assert title_of(instance, object_type) == "Acme"
    object_type["title_key"] = None
    assert title_of(instance, object_type) == "7"
    assert title_of({"id": 3}, None) == "3"


def test_title_of_resolves_via_property_mapping_column() -> None:
    instance = {"customer_name": "Globex"}
    object_type = {
        "title_key": "name",
        "primary_key": "id",
        "property_mapping": {"id": "id", "name": "customer_name"},
    }
    assert title_of(instance, object_type) == "Globex"


def test_validate_ot_metadata_requires_pk_in_mapping() -> None:
    with pytest.raises(ValueError, match="primary_key"):
        validate_ot_metadata(
            property_mapping={"name": "name"},
            primary_key="id",
            title_key=None,
            lifecycle_status="experimental",
            visibility="normal",
        )
    with pytest.raises(ValueError, match="title_key"):
        validate_ot_metadata(
            property_mapping={"id": "id"},
            primary_key="id",
            title_key="name",
            lifecycle_status="experimental",
            visibility="normal",
        )
    validate_ot_metadata(
        property_mapping={"id": "id", "name": "name"},
        primary_key="id",
        title_key="name",
        lifecycle_status="active",
        visibility="prominent",
    )


def test_create_object_type_persists_primary_and_title_keys(msmith_token: str) -> None:
    dataset_name = _unique_name("ot_meta_src")
    status, registration = _request(
        "POST",
        f"{CONNECTIVITY}/sources",
        token=msmith_token,
        body={"name": dataset_name, "base_url": "http://reviews-api:8000/reviews.json"},
    )
    assert status == 200, registration
    status, sync_result = _request(
        "POST", f"{CONNECTIVITY}/sync", token=msmith_token, body={"dataset": dataset_name}
    )
    assert status == 200, sync_result

    type_name = _unique_name("MetaType")
    status, created = _request(
        "POST",
        holon_url("/object-types"),
        token=msmith_token,
        body={
            "name": type_name,
            "source_dataset_urn": sync_result["dataset_urn"],
            "property_mapping": {"id": "id", "author": "author", "rating": "rating"},
            "primary_key": "id",
            "title_key": "author",
            "plural_display_name": "MetaTypes",
            "lifecycle_status": "experimental",
            "visibility": "prominent",
            "icon": "cube",
        },
    )
    assert status == 201, created
    assert created["primary_key"] == "id", created
    assert created["title_key"] == "author", created
    assert created["plural_display_name"] == "MetaTypes", created
    assert created["visibility"] == "prominent", created
    assert created["icon"] == "cube", created

    status, fetched = _request("GET", ontology_url(f"/objectTypes/{type_name}"), token=msmith_token)
    assert status == 200, fetched
    assert fetched["title_key"] == "author", fetched


def test_publish_rejects_primary_key_change_when_active(msmith_token: str) -> None:
    status, customers = _request("GET", ontology_url("/objectTypes/Customer"), token=msmith_token)
    assert status == 200, customers
    status, draft = _request(
        "POST",
        ontology_url("/objectTypes/Customer/versions"),
        token=msmith_token,
        body={
            "description": customers.get("description") or "customer",
            "implements": customers.get("implements") or [],
            "markings": customers.get("markings") or [],
            "property_types": customers.get("property_types") or {},
            "derived_properties": customers.get("derived_properties") or {},
            "primary_key": customers.get("primary_key") or "id",
            "title_key": customers.get("title_key") or "name",
            "lifecycle_status": "active",
            "visibility": customers.get("visibility") or "normal",
        },
    )
    assert status in (200, 201), draft
    status, published = _request(
        "POST", ontology_url(f"/objectTypes/Customer/versions/{draft['version']}/publish"), token=msmith_token
    )
    assert status == 200, published

    status, bad_draft = _request(
        "POST",
        ontology_url("/objectTypes/Customer/versions"),
        token=msmith_token,
        body={
            "description": "try pk change",
            "primary_key": "name",
            "title_key": "name",
            "lifecycle_status": "active",
        },
    )
    assert status in (200, 201), bad_draft
    status, refused = _request(
        "POST",
        ontology_url(f"/objectTypes/Customer/versions/{bad_draft['version']}/publish"),
        token=msmith_token,
    )
    assert status == 400, refused
    assert "primary_key" in str(refused), refused
