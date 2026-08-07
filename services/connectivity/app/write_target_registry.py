"""Write Target registry — the declarative counterpart, on the write
side, to `generic_source_registry.py`'s no-code REST connector on the
read side. Scoped deliberately narrower: a write target names a real
Postgres table (in the upstream *source* database, `SOURCE_DB_URL`, not
this service's own `app.state.pool`) and an explicit allow-list of
`{property: column}` pairs a governed Action is permitted to write —
never an arbitrary REST API. Writing back to a third-party REST API
generically isn't a solvable problem the same way reading from one is:
there's no consistent PATCH/PUT convention across vendors the way there
is a consistent "GET + optional pagination" shape, so this is bounded to
the one real writeback mechanism this build already has (direct
Postgres), not extended to the no-code REST connector.

The only mutation path into a source system remains a governed ontology
Action — see `connector.py`'s module docstring ("a connector MUST NEVER
write back to its source") and `main.py`'s `POST /source/{dataset_name}
/{instance_id}/write`, gated to Automation's Workflow Engine exactly
like the pre-existing `POST /source/customers/{id}/close-account`.
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


async def apply_write(
    pool: asyncpg.Pool, source_db_url: str, *, tenant_id: str, dataset_name: str, instance_id: str, edits: dict[str, Any]
) -> dict:
    """The generic counterpart to `close_source_customer_account`'s
    inline raw SQL — resolves the declarative `write_target` config,
    validates every edited property is actually allow-listed (never
    write a column an admin didn't explicitly name), then builds one
    parameterized `UPDATE` statement. A fresh `asyncpg.connect` per call
    against `source_db_url`, same as the pre-existing closeAccount/
    get-source-customer endpoints — this is a rare, human-approval-gated
    path, not a hot one, so a per-call connection is the same acceptable
    trade-off it already was there.
    """
    target = await get_write_target(pool, tenant_id, dataset_name)
    if target is None:
        raise UnknownWriteTargetError(f"no write target registered for dataset {dataset_name!r}")

    unknown = set(edits) - set(target["allowed_properties"])
    if unknown:
        raise WriteTargetConfigError(
            f"propert{'y' if len(unknown) == 1 else 'ies'} not allow-listed for {dataset_name!r}: {sorted(unknown)}"
        )
    if not edits:
        raise WriteTargetConfigError("no edits to apply")

    # `table_name`/`id_column`/`columns` are interpolated directly (asyncpg
    # has no parameter-binding for identifiers) — safe here specifically
    # because they only ever come from `write_target`, a governance-gated
    # registry (`register_write_target` requires the same admin-tier
    # authorization every other ontology-write endpoint does), never from
    # this call's own caller-supplied `edits`. Every actual *value* is
    # still bound as a real parameter below.
    columns = [target["allowed_properties"][name] for name in edits]
    values = list(edits.values())
    set_clause = ", ".join(f"{col} = ${i + 1}" for i, col in enumerate(columns))
    id_placeholder = len(values) + 1

    # `instance_id` always arrives as a string (a URL path segment, all
    # the way from the Action's own instance URN), but asyncpg refuses to
    # bind a Python `str` against an `INTEGER` id column (no implicit
    # cast) — every numeric-keyed write target would 500 without this.
    # Same try-int-else-string coercion `resolver.fetch_generic`'s
    # `id_value` already uses for exactly this reason.
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
