"""Unit tests for the in-process ontology definition cache (SAS R7.7)."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "services" / "knowledge"))

from app.ontology import definition_cache  # noqa: E402


def setup_function() -> None:
    definition_cache.clear()


def test_put_get_returns_a_copy() -> None:
    definition_cache.put("ot:hl:acme:main:object-type:Customer", {"name": "Customer", "markings": []})
    first = definition_cache.get("ot:hl:acme:main:object-type:Customer")
    first["markings"] = ["mutated"]
    second = definition_cache.get("ot:hl:acme:main:object-type:Customer")
    assert second == {"name": "Customer", "markings": []}


def test_invalidate_object_type_drops_list_and_props() -> None:
    urn = "hl:acme:main:object-type:Customer"
    definition_cache.put(definition_cache.object_type_key(urn), {"name": "Customer"})
    definition_cache.put(definition_cache.property_classifications_key(urn), {"email": "confidential"})
    definition_cache.put(definition_cache.object_type_list_key("acme"), [{"name": "Customer"}])
    definition_cache.put(definition_cache.object_type_dataset_key("acme", "hl:acme:main:dataset:customers"), {"name": "Customer"})

    definition_cache.invalidate_object_type(urn=urn, tenant_id="acme")

    assert definition_cache.get(definition_cache.object_type_key(urn)) is None
    assert definition_cache.get(definition_cache.property_classifications_key(urn)) is None
    assert definition_cache.get(definition_cache.object_type_list_key("acme")) is None
    assert definition_cache.get(definition_cache.object_type_dataset_key("acme", "hl:acme:main:dataset:customers")) is None


def test_has_is_false_after_expiry(monkeypatch) -> None:
    definition_cache.put("k", {"ok": True}, ttl_seconds=0.0)
    assert definition_cache.has("k") is False
    assert definition_cache.get("k") is None
