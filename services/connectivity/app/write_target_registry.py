"""Write Target registry — declarative writeback registry to target databases.

Registers Postgres write targets and allowed property-to-column mappings for writeback actions.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS write_target (
    tenant_id TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    id_column TEXT NOT NULL,
    allowed_properties JSONB NOT NULL,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, dataset_name)
);
"""


class WriteTargetConfigError(ValueError):
    pass


class UnknownWriteTargetError(ValueError):
    pass


class InstanceNotFoundError(ValueError):
    pass


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def register_write_target(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    dataset_name: str,
    table_name: str,
    id_column: str,
    allowed_properties: dict[str, str],
    created_by_urn: str,
) -> dict:
    if not allowed_properties:
        raise WriteTargetConfigError("allowed_properties must name at least one property")
    await pool.execute(
        """
        INSERT INTO write_target (tenant_id, dataset_name, table_name, id_column, allowed_properties, created_by_urn)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6)
        ON CONFLICT (tenant_id, dataset_name) DO UPDATE SET
            table_name = EXCLUDED.table_name,
            id_column = EXCLUDED.id_column,
            allowed_properties = EXCLUDED.allowed_properties
        """,
        tenant_id, dataset_name, table_name, id_column, json.dumps(allowed_properties), created_by_urn,
    )
    return await get_write_target(pool, tenant_id, dataset_name)


def _parse_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result["allowed_properties"], str):
        result["allowed_properties"] = json.loads(result["allowed_properties"])
    return result


async def get_write_target(pool: asyncpg.Pool, tenant_id: str, dataset_name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM write_target WHERE tenant_id = $1 AND dataset_name = $2", tenant_id, dataset_name
    )
    return _parse_row(row) if row else None


async def list_write_targets(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM write_target WHERE tenant_id = $1 ORDER BY dataset_name", tenant_id)
    return [_parse_row(row) for row in rows]


async def delete_write_target(pool: asyncpg.Pool, tenant_id: str, dataset_name: str) -> None:
    await pool.execute(
        "DELETE FROM write_target WHERE tenant_id = $1 AND dataset_name = $2", tenant_id, dataset_name
    )


async def apply_write(
    pool: asyncpg.Pool, source_db_url: str, *, tenant_id: str, dataset_name: str, instance_id: str, edits: dict[str, Any]
) -> dict:
    """Apply allow-listed edits to a registered write target in source database."""
    target = await get_write_target(pool, tenant_id, dataset_name)
    if target is None:
        raise UnknownWriteTargetError(f"no write target registered for dataset {dataset_name!r}")

    unknown = set(edits) - set(target["allowed_properties"])
    # Filter out properties not in allowed_properties
    filtered = {k: v for k, v in edits.items() if k in target["allowed_properties"]}
    if not filtered:
        raise WriteTargetConfigError(
            f"no allow-listed edits to apply for {dataset_name!r}"
            + (f" (dropped: {sorted(unknown)})" if unknown else "")
        )
    edits = filtered

    columns = [target["allowed_properties"][name] for name in edits]
    values = list(edits.values())
    set_clause = ", ".join(f"{col} = ${i + 1}" for i, col in enumerate(columns))
    id_placeholder = len(values) + 1

    try:
        typed_instance_id: object = int(instance_id)
    except ValueError:
        typed_instance_id = instance_id

    conn = await asyncpg.connect(source_db_url)
    try:
        row = await conn.fetchrow(
            f"UPDATE {target['table_name']} SET {set_clause} WHERE {target['id_column']} = ${id_placeholder} RETURNING *",
            *values, typed_instance_id,
        )
    finally:
        await conn.close()

    if row is None:
        raise InstanceNotFoundError(f"{dataset_name}/{instance_id} not found in source")
    return dict(row)
