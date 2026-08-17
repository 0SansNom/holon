"""Tests for Typed Property Values."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock

REPO_ROOT = Path(__file__).resolve().parents[3]
KNOWLEDGE_DIR = REPO_ROOT / "services" / "knowledge"
LIBS = REPO_ROOT / "libs"


def _import_typed_values():
    sys.path.insert(0, str(LIBS))
    sys.path.insert(0, str(KNOWLEDGE_DIR))
    app = types.ModuleType("app")
    app.__path__ = [str(KNOWLEDGE_DIR / "app")]
    sys.modules["app"] = app
    ontology_pkg = types.ModuleType("app.ontology")
    ontology_pkg.__path__ = [str(KNOWLEDGE_DIR / "app" / "ontology")]
    sys.modules["app.ontology"] = ontology_pkg

    value_types = types.ModuleType("app.ontology.value_types")

    async def get_value_type(_pool, _tenant, name: str) -> Optional[dict]:
        if name == "String":
            return {"name": "String", "base_type": "string", "format_regex": None, "constraints": []}
        if name == "Integer":
            return {"name": "Integer", "base_type": "integer", "format_regex": None, "constraints": []}
        return None

    def validate_value(value: Any, value_type_row: dict) -> Optional[str]:
        base = value_type_row["base_type"]
        if base == "string" and not isinstance(value, str):
            return "expected string"
        if base == "integer" and not isinstance(value, int):
            return "expected integer"
        return None

    value_types.get_value_type = get_value_type
    value_types.validate_value = validate_value
    sys.modules["app.ontology.value_types"] = value_types

    shared = types.ModuleType("app.ontology.shared_property_types")

    async def get_shared_property_type(_pool, _tenant, api_name: str) -> Optional[dict]:
        if api_name == "Email":
            return {"api_name": "Email", "value_type": "String", "struct_properties": None}
        if api_name == "Address":
            return {
                "api_name": "Address",
                "value_type": None,
                "struct_properties": {
                    "city": {"kind": "value_type", "value_type": "String"},
                    "zip": {"kind": "value_type", "value_type": "String"},
                },
            }
        return None

    shared.get_shared_property_type = get_shared_property_type
    sys.modules["app.ontology.shared_property_types"] = shared

    # Force re-import so stubs win even if a prior test loaded the real module.
    sys.modules.pop("app.ontology.typed_values", None)
    from app.ontology import typed_values  # noqa: WPS433

    return typed_values


def test_struct_rejects_unknown_field_and_bad_leaf_type() -> None:
    tv = _import_typed_values()
    pool = AsyncMock()
    rule = {
        "kind": "struct",
        "properties": {
            "city": {"kind": "value_type", "value_type": "String"},
            "zip": {"kind": "value_type", "value_type": "String"},
        },
    }

    async def _run() -> None:
        err = await tv.validate_typed_property_value(
            pool, "acme", rule, {"city": "Paris", "extra": 1}, property_name="address"
        )
        assert err and "unknown struct field" in err

        err = await tv.validate_typed_property_value(
            pool,
            "acme",
            rule,
            {"city": "Paris", "extra": 1},
            property_name="address",
            allow_unknown_struct_fields=True,
        )
        assert err is None

        err = await tv.validate_typed_property_value(
            pool, "acme", rule, {"city": 12, "zip": "75001"}, property_name="address"
        )
        assert err and "expected string" in err

        err = await tv.validate_typed_property_value(
            pool, "acme", rule, {"city": "Paris", "zip": "75001"}, property_name="address"
        )
        assert err is None

    asyncio.run(_run())


def test_array_of_struct_and_shared_struct_spt() -> None:
    tv = _import_typed_values()
    pool = AsyncMock()
    array_rule = {
        "kind": "array",
        "element": {
            "kind": "struct",
            "properties": {"label": {"kind": "value_type", "value_type": "String"}},
        },
    }

    async def _run() -> None:
        err = await tv.validate_typed_property_value(
            pool, "acme", array_rule, [{"label": "a"}, {"label": "b"}], property_name="tags"
        )
        assert err is None

        err = await tv.validate_typed_property_value(
            pool, "acme", array_rule, [{"label": 1}], property_name="tags"
        )
        assert err and "expected string" in err

        spt_rule = {"kind": "shared_property_type", "shared_property_type": "Address"}
        err = await tv.validate_typed_property_value(
            pool, "acme", spt_rule, {"city": "Lyon", "zip": "69001"}, property_name="home"
        )
        assert err is None

        unique_rule = {
            "kind": "array",
            "unique_elements": True,
            "element": {"kind": "value_type", "value_type": "String"},
        }
        err = await tv.validate_typed_property_value(
            pool, "acme", unique_rule, ["a", "b"], property_name="tags"
        )
        assert err is None
        err = await tv.validate_typed_property_value(
            pool, "acme", unique_rule, ["a", "a"], property_name="tags"
        )
        assert err and "unique" in err

    asyncio.run(_run())


def test_value_type_casts_and_object_row_partition() -> None:
    tv = _import_typed_values()
    pool = AsyncMock()

    async def _run() -> None:
        errs = await tv.validate_value_type_casts(
            pool,
            "acme",
            casts={"email": "String", "qty": "Integer"},
            row={"email": "a@b.co", "qty": "nope"},
            row_index=2,
        )
        assert len(errs) == 1
        assert errs[0]["row_index"] == 2
        assert errs[0]["column"] == "qty"

        unknown = await tv.validate_value_type_casts(
            pool, "acme", casts={"x": "Missing"}, row={"x": "1"}, row_index=0
        )
        assert unknown and "unknown value_type" in unknown[0]["detail"]

        property_types = {"email": {"kind": "value_type", "value_type": "String"}}
        mapping = {"email": "email_col"}
        valid, invalid = await tv.partition_rows_by_property_types(
            pool,
            "acme",
            property_mapping=mapping,
            property_types=property_types,
            rows=[
                {"id": "1", "email_col": "ok"},
                {"id": "2", "email_col": 99},
            ],
        )
        assert [r["id"] for r in valid] == ["1"]
        assert [r["id"] for r in invalid] == ["2"]

    asyncio.run(_run())
