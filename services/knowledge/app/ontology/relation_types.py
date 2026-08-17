"""RelationType registry — FK, join-dataset (M:N), and object-backed links.

Unlike ObjectType, a workspace admin can register a RelationType at runtime
directly (no draft step); cardinality/endpoint/storage validation is
enforced synchronously here. Traversal reads this registry live.

Foundry Link Type metadata lives here too: each side has its own
display/plural/API name + visibility; the type itself carries
lifecycle_status and type_classes.
"""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

from .object_types import VALID_VISIBILITIES, get_object_type
from .type_classes import normalize_type_classes
from .lifecycle import normalize_deprecation_metadata
from .urns import object_type_urn, relation_type_urn

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


async def get_relation_type(pool: asyncpg.Pool, urn: str) -> dict | None:
    row = await pool.fetchrow("SELECT * FROM relation_type WHERE urn = $1", urn)
    return _row_to_dict(row) if row else None


async def list_relation_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM relation_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_row_to_dict(row) for row in rows]


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


async def create_relation_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    source_object_type: str,
    target_object_type: str,
    source_property: str,
    target_property: str,
    cardinality: str,
    storage_kind: str = "foreign_key",
    join_dataset_urn: Optional[str] = None,
    join_source_column: Optional[str] = None,
    join_target_column: Optional[str] = None,
    mid_object_type: Optional[str] = None,
    mid_source_property: Optional[str] = None,
    mid_target_property: Optional[str] = None,
    source_display_name: str = "",
    source_plural_display_name: str = "",
    source_api_name: Optional[str] = None,
    source_visibility: str = "normal",
    target_display_name: str = "",
    target_plural_display_name: str = "",
    target_api_name: Optional[str] = None,
    target_visibility: str = "normal",
    lifecycle_status: str = "experimental",
    type_classes: Optional[list[str]] = None,
    project_urn: Optional[str] = None,
    deprecation_reason: Optional[str] = None,
    deprecation_deadline=None,
    replacement_urn: Optional[str] = None,
) -> dict:
    if cardinality not in VALID_CARDINALITIES:
        raise ValueError(f"invalid cardinality: {cardinality!r} (must be one of {sorted(VALID_CARDINALITIES)})")
    if not target_property:
        raise ValueError("target_property is required — the reverse-direction accessor name")
    dep = normalize_deprecation_metadata(
        lifecycle_status,
        deprecation_reason=deprecation_reason,
        deprecation_deadline=deprecation_deadline,
        replacement_urn=replacement_urn,
    )

    source_urn = object_type_urn(tenant_id, workspace_id, source_object_type)
    if await get_object_type(pool, source_urn) is None:
        raise ValueError(f"source_object_type does not exist: {source_object_type}")

    target_urn = object_type_urn(tenant_id, workspace_id, target_object_type)
    if await get_object_type(pool, target_urn) is None:
        raise ValueError(f"target_object_type does not exist: {target_object_type}")

    mid_urn = None
    if mid_object_type:
        mid_urn = object_type_urn(tenant_id, workspace_id, mid_object_type)
        if await get_object_type(pool, mid_urn) is None:
            raise ValueError(f"mid_object_type does not exist: {mid_object_type}")

    _validate_storage(
        storage_kind=storage_kind,
        cardinality=cardinality,
        source_property=source_property or "",
        join_dataset_urn=join_dataset_urn,
        join_source_column=join_source_column,
        join_target_column=join_target_column,
        mid_object_type_urn=mid_urn,
        mid_source_property=mid_source_property,
        mid_target_property=mid_target_property,
    )

    # FK storage still needs a non-empty source_property; join/object_backed
    # may use a placeholder when the join is not property-on-source.
    effective_source_property = source_property or (
        join_source_column if storage_kind == "join_dataset" else mid_source_property or "_"
    )

    src_disp, src_plural, src_api, src_vis = _normalize_side_metadata(
        display_name=source_display_name,
        plural_display_name=source_plural_display_name,
        api_name=source_api_name if source_api_name is not None else _local_accessor(name),
        visibility=source_visibility,
        side="source",
    )
    tgt_disp, tgt_plural, tgt_api, tgt_vis = _normalize_side_metadata(
        display_name=target_display_name,
        plural_display_name=target_plural_display_name,
        api_name=target_api_name if target_api_name is not None else target_property,
        visibility=target_visibility,
        side="target",
    )
    classes = _normalize_type_classes(type_classes)

    urn = relation_type_urn(tenant_id, workspace_id, name)
    await pool.execute(
        """
        INSERT INTO relation_type (
            urn, tenant_id, name, source_object_type_urn, target_object_type_urn,
            source_property, target_property, cardinality, storage_kind,
            join_dataset_urn, join_source_column, join_target_column,
            mid_object_type_urn, mid_source_property, mid_target_property,
            source_display_name, source_plural_display_name, source_api_name, source_visibility,
            target_display_name, target_plural_display_name, target_api_name, target_visibility,
            lifecycle_status, type_classes, project_urn,
            deprecation_reason, deprecation_deadline, replacement_urn
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
            $16, $17, $18, $19, $20, $21, $22, $23, $24, $25::jsonb, $26,
            $27, $28, $29
        )
        """,
        urn,
        tenant_id,
        name,
        source_urn,
        target_urn,
        effective_source_property,
        target_property,
        cardinality,
        storage_kind,
        join_dataset_urn,
        join_source_column,
        join_target_column,
        mid_urn,
        mid_source_property,
        mid_target_property,
        src_disp,
        src_plural,
        src_api,
        src_vis,
        tgt_disp,
        tgt_plural,
        tgt_api,
        tgt_vis,
        dep["lifecycle_status"],
        json.dumps(classes),
        project_urn.strip() if isinstance(project_urn, str) and project_urn.strip() else None,
        dep["deprecation_reason"],
        dep["deprecation_deadline"],
        dep["replacement_urn"],
    )
    return await get_relation_type(pool, urn)


async def update_relation_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    target_property: Optional[str] = None,
    cardinality: Optional[str] = None,
    storage_kind: Optional[str] = None,
    join_dataset_urn: Optional[str] = None,
    join_source_column: Optional[str] = None,
    join_target_column: Optional[str] = None,
    mid_object_type: Optional[str] = None,
    mid_source_property: Optional[str] = None,
    mid_target_property: Optional[str] = None,
    source_display_name: Optional[str] = None,
    source_plural_display_name: Optional[str] = None,
    source_api_name: Optional[str] = None,
    source_visibility: Optional[str] = None,
    target_display_name: Optional[str] = None,
    target_plural_display_name: Optional[str] = None,
    target_api_name: Optional[str] = None,
    target_visibility: Optional[str] = None,
    lifecycle_status: Optional[str] = None,
    type_classes: Optional[list[str]] = None,
    project_urn: Optional[str] = None,
    clear_project_urn: bool = False,
    deprecation_reason: Optional[str] = None,
    deprecation_deadline=None,
    replacement_urn: Optional[str] = None,
) -> dict:
    """Partial update. Source/target ObjectType and `source_property` are
    structural identity and are not accepted here.
    """
    urn = relation_type_urn(tenant_id, workspace_id, name)
    current = await get_relation_type(pool, urn)
    if current is None:
        raise ValueError(f"unknown RelationType: {name!r}")

    new_target_property = current["target_property"] if target_property is None else target_property
    new_cardinality = current["cardinality"] if cardinality is None else cardinality
    new_storage = current.get("storage_kind") or "foreign_key"
    if storage_kind is not None:
        new_storage = storage_kind
    new_join_urn = current.get("join_dataset_urn") if join_dataset_urn is None else join_dataset_urn
    new_join_src = current.get("join_source_column") if join_source_column is None else join_source_column
    new_join_tgt = current.get("join_target_column") if join_target_column is None else join_target_column
    new_mid_src = current.get("mid_source_property") if mid_source_property is None else mid_source_property
    new_mid_tgt = current.get("mid_target_property") if mid_target_property is None else mid_target_property
    new_mid_urn = current.get("mid_object_type_urn")
    if mid_object_type is not None:
        new_mid_urn = object_type_urn(tenant_id, workspace_id, mid_object_type)
        if await get_object_type(pool, new_mid_urn) is None:
            raise ValueError(f"mid_object_type does not exist: {mid_object_type}")

    if not new_target_property:
        raise ValueError("target_property is required — the reverse-direction accessor name")
    if new_cardinality not in VALID_CARDINALITIES:
        raise ValueError(f"invalid cardinality: {new_cardinality!r} (must be one of {sorted(VALID_CARDINALITIES)})")

    _validate_storage(
        storage_kind=new_storage,
        cardinality=new_cardinality,
        source_property=current["source_property"] or "",
        join_dataset_urn=new_join_urn,
        join_source_column=new_join_src,
        join_target_column=new_join_tgt,
        mid_object_type_urn=new_mid_urn,
        mid_source_property=new_mid_src,
        mid_target_property=new_mid_tgt,
    )

    src_disp, src_plural, src_api, src_vis = _normalize_side_metadata(
        display_name=current.get("source_display_name") or "" if source_display_name is None else source_display_name,
        plural_display_name=(
            current.get("source_plural_display_name") or ""
            if source_plural_display_name is None
            else source_plural_display_name
        ),
        api_name=(
            (current.get("source_api_name") or _local_accessor(name))
            if source_api_name is None
            else source_api_name
        ),
        visibility=(current.get("source_visibility") or "normal") if source_visibility is None else source_visibility,
        side="source",
    )
    tgt_disp, tgt_plural, tgt_api, tgt_vis = _normalize_side_metadata(
        display_name=current.get("target_display_name") or "" if target_display_name is None else target_display_name,
        plural_display_name=(
            current.get("target_plural_display_name") or ""
            if target_plural_display_name is None
            else target_plural_display_name
        ),
        api_name=(
            (current.get("target_api_name") or current["target_property"])
            if target_api_name is None
            else target_api_name
        ),
        visibility=(current.get("target_visibility") or "normal") if target_visibility is None else target_visibility,
        side="target",
    )
    new_status = current.get("lifecycle_status") or "experimental"
    if lifecycle_status is not None:
        new_status = lifecycle_status
    new_dep_reason = (
        current.get("deprecation_reason") if deprecation_reason is None else deprecation_reason
    )
    new_dep_deadline = (
        current.get("deprecation_deadline") if deprecation_deadline is None else deprecation_deadline
    )
    new_replacement = current.get("replacement_urn") if replacement_urn is None else replacement_urn
    dep = normalize_deprecation_metadata(
        new_status,
        deprecation_reason=new_dep_reason,
        deprecation_deadline=new_dep_deadline,
        replacement_urn=new_replacement,
    )
    classes = (
        list(current.get("type_classes") or [])
        if type_classes is None
        else _normalize_type_classes(type_classes)
    )
    if clear_project_urn:
        new_project_urn = None
    elif project_urn is not None:
        new_project_urn = project_urn.strip() or None
    else:
        new_project_urn = current.get("project_urn")

    await pool.execute(
        """
        UPDATE relation_type SET
            target_property = $1, cardinality = $2, storage_kind = $3,
            join_dataset_urn = $4, join_source_column = $5, join_target_column = $6,
            mid_object_type_urn = $7, mid_source_property = $8, mid_target_property = $9,
            source_display_name = $10, source_plural_display_name = $11,
            source_api_name = $12, source_visibility = $13,
            target_display_name = $14, target_plural_display_name = $15,
            target_api_name = $16, target_visibility = $17,
            lifecycle_status = $18, type_classes = $19::jsonb, project_urn = $20,
            deprecation_reason = $21, deprecation_deadline = $22, replacement_urn = $23
        WHERE urn = $24
        """,
        new_target_property,
        new_cardinality,
        new_storage,
        new_join_urn,
        new_join_src,
        new_join_tgt,
        new_mid_urn,
        new_mid_src,
        new_mid_tgt,
        src_disp,
        src_plural,
        src_api,
        src_vis,
        tgt_disp,
        tgt_plural,
        tgt_api,
        tgt_vis,
        dep["lifecycle_status"],
        json.dumps(classes),
        new_project_urn,
        dep["deprecation_reason"],
        dep["deprecation_deadline"],
        dep["replacement_urn"],
        urn,
    )
    return await get_relation_type(pool, urn)


async def delete_relation_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
) -> None:
    urn = relation_type_urn(tenant_id, workspace_id, name)
    current = await get_relation_type(pool, urn)
    if current is None:
        raise ValueError(f"unknown RelationType: {name!r}")
    if (current.get("lifecycle_status") or "experimental") == "active":
        raise ValueError(
            "cannot delete an active RelationType — set lifecycle_status to deprecated (or experimental) first"
        )
    await pool.execute("DELETE FROM relation_type WHERE urn = $1", urn)


async def cascade_lifecycle_from_object_type(
    conn: asyncpg.Connection,
    *,
    object_type_urn: str,
    lifecycle_status: str,
) -> int:
    """Foundry: when an ObjectType becomes experimental, example, or deprecated,
    linked RelationTypes are forced to the same status (prevents active links on
    non-active types).

    Returns the number of RelationType rows updated.
    """
    if lifecycle_status not in ("experimental", "deprecated", "example"):
        return 0
    if lifecycle_status in ("experimental", "example"):
        result = await conn.execute(
            """
            UPDATE relation_type SET
                lifecycle_status = $2,
                deprecation_reason = NULL,
                deprecation_deadline = NULL,
                replacement_urn = NULL
            WHERE (source_object_type_urn = $1 OR target_object_type_urn = $1
                   OR mid_object_type_urn = $1)
              AND lifecycle_status NOT IN ('experimental', 'deprecated', 'example')
            """,
            object_type_urn,
            lifecycle_status,
        )
    else:
        result = await conn.execute(
            """
            UPDATE relation_type SET
                lifecycle_status = 'deprecated',
                deprecation_reason = COALESCE(
                    NULLIF(deprecation_reason, ''),
                    'Cascaded from linked ObjectType deprecation'
                ),
                deprecation_deadline = COALESCE(
                    deprecation_deadline,
                    (CURRENT_DATE + INTERVAL '90 days')::date
                )
            WHERE (source_object_type_urn = $1 OR target_object_type_urn = $1
                   OR mid_object_type_urn = $1)
              AND lifecycle_status <> 'deprecated'
            """,
            object_type_urn,
        )
    try:
        return int(str(result).split()[-1])
    except (ValueError, IndexError):
        return 0
