"""RelationType storage / side-metadata helpers."""
from __future__ import annotations

import json
from typing import Optional


from .object_types import VALID_VISIBILITIES
from .type_classes import normalize_type_classes


VALID_CARDINALITIES = {"one_to_one", "one_to_many", "many_to_one", "many_to_many"}
VALID_STORAGE_KINDS = {"foreign_key", "join_dataset", "object_backed"}


def _parse_jsonb(value, *, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default
    return default

def _row_to_dict(row) -> dict:
    data = dict(row)
    data["type_classes"] = list(_parse_jsonb(data.get("type_classes"), default=[]))
    return data

def _local_accessor(name: str) -> str:
    """`Order.customer` → `customer`; bare `customer` stays `customer`."""
    return name.split(".", 1)[-1]

def _normalize_side_metadata(
    *,
    display_name: str,
    plural_display_name: str,
    api_name: str,
    visibility: str,
    side: str,
) -> tuple[str, str, str, str]:
    if visibility not in VALID_VISIBILITIES:
        raise ValueError(
            f"{side}_visibility must be one of {sorted(VALID_VISIBILITIES)} (got {visibility!r})"
        )
    api = (api_name or "").strip()
    if not api:
        raise ValueError(f"{side}_api_name is required")
    return display_name or "", plural_display_name or "", api, visibility

def _normalize_type_classes(type_classes: Optional[list[str]]) -> list[str]:
    return normalize_type_classes(type_classes)

def _validate_storage(
    *,
    storage_kind: str,
    cardinality: str,
    source_property: str,
    join_dataset_urn: Optional[str],
    join_source_column: Optional[str],
    join_target_column: Optional[str],
    mid_object_type_urn: Optional[str],
    mid_source_property: Optional[str],
    mid_target_property: Optional[str],
) -> None:
    if storage_kind not in VALID_STORAGE_KINDS:
        raise ValueError(f"invalid storage_kind: {storage_kind!r} (must be one of {sorted(VALID_STORAGE_KINDS)})")
    if storage_kind == "foreign_key":
        if not source_property:
            raise ValueError("source_property is required for foreign_key storage")
        return
    if storage_kind == "join_dataset":
        if cardinality != "many_to_many":
            raise ValueError("join_dataset storage requires cardinality many_to_many")
        if not join_dataset_urn or not join_source_column or not join_target_column:
            raise ValueError("join_dataset requires join_dataset_urn, join_source_column, join_target_column")
        return
    # object_backed
    if not mid_object_type_urn or not mid_source_property or not mid_target_property:
        raise ValueError("object_backed requires mid_object_type_urn, mid_source_property, mid_target_property")

