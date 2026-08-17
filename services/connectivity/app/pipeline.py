"""Pipeline / Transform DAG.

Today every connector is one hop: source system -> Iceberg raw,
catalogued once, done. Foundry's own Python transforms are *derived
datasets computed from other datasets*, forming a DAG with automatic
lineage. A `PipelineDefinition` here is a named, versioned (via
`ON CONFLICT ... DO UPDATE`, same upsert shape `plugin_registry.py`
already uses) ordered list of `TransformStep`s; each step reads one
existing Iceberg table (`iceberg_reader.read_table`), applies a
registered  **Function** to every row via Knowledge's
`POST /functions/{name}/invoke` — the same registered-computation
mechanism Interfaces/Actions already use, unified rather than parallel,
just a third call site with its own row -> row contract (see that
endpoint's own docstring) — and writes the result as a new Iceberg
snapshot (`iceberg_writer.write_snapshot`, completely unmodified).

`main.py`'s `POST /pipelines/{name}/run` is what actually drives a step:
it reads, invokes, writes, then calls the *same* `_finalize_sync` every
core connector's `/sync` already calls, so Catalog/classification-
propagation all pick up a pipeline's output through the existing
`connectivity.sync.completed` consumer path — the one deliberate,
additive extension to that path is `source_dataset_version_urn` on the
event payload, letting `catalog._catalogue_sync` record a real
dataset -> dataset `derived_from` lineage edge, not just the `maps_to`
edge every synced dataset already got. This module owns the pipeline
*definition* and DAG-shape validation; the actual read/invoke/write/
finalize sequence lives in `main.py`, next to `_finalize_sync` and the
app-level `httpx` client it already has no equivalent of today (a new
one is created per pipeline run — pipelines are not a hot path).

Honest scope boundary, stated plainly: steps execute strictly in the
order declared, not via a real topological sort over a general graph —
`_validate_steps` only rejects a step naming a *later* step's own output
as its input (a forward reference, structurally impossible to satisfy)
and duplicate step names. A pipeline whose steps are already listed in
a valid dependency order runs correctly; nothing here reorders steps
for you. Also, like `plugin_registry.py`'s own connector plugins, a
pipeline's output dataset lands in Iceberg with a real snapshot, a real
event, and now real lineage — but nothing here auto-registers it as a
queryable ObjectType in Knowledge's ontology; that's still a deliberate,
separate step, the same gap connector plugins already have and state
plainly rather than paper over.
"""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS pipeline_definition (
    name TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    steps JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS pipeline_run (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    pipeline_name TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    step_results JSONB NOT NULL DEFAULT '[]',
    error TEXT
);
"""

REQUIRED_STEP_FIELDS = ("step_name", "input_dataset", "function_name", "output_dataset")


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def _validate_steps(steps: list[dict]) -> None:
    """DAG-shape validation, not dataset existence — a step's input
    might be a raw connector dataset (Connectivity has no fixed registry
    of every one a plugin could ever produce) or another pipeline's own
    output; either way, whether it's real is discovered at *run* time
    (`iceberg_reader.read_table` raises `NoSuchTableError` there, not
    here), the same "validate what's checkable now, let execution be the
    source of truth for the rest" precedent `propose_object_type_version`
    already sets for `implements`/`markings` in Knowledge.
    """
    if not steps:
        raise ValueError("a pipeline needs at least one step")

    step_names: set[str] = set()
    outputs_so_far: set[str] = set()
    all_outputs = {step["output_dataset"] for step in steps if step.get("output_dataset")}

    for step in steps:
        missing = [field for field in REQUIRED_STEP_FIELDS if not step.get(field)]
        if missing:
            raise ValueError(f"step {step.get('step_name', '<unnamed>')!r} is missing required field(s): {missing}")
        if step["step_name"] in step_names:
            raise ValueError(f"duplicate step_name: {step['step_name']!r}")
        step_names.add(step["step_name"])

        if step["input_dataset"] == step["output_dataset"]:
            raise ValueError(f"step {step['step_name']!r} cannot read and write the same dataset")

        if step["input_dataset"] in all_outputs and step["input_dataset"] not in outputs_so_far:
            # input_dataset names a dataset only a *later* step in this
            # same pipeline produces — a forward reference this linear
            # executor can never satisfy, caught at definition time
            # rather than failing mid-run on a missing table.
            later_producer = next(s["step_name"] for s in steps if s["output_dataset"] == step["input_dataset"])
            raise ValueError(
                f"step {step['step_name']!r} reads {step['input_dataset']!r}, "
                f"which is produced later by step {later_producer!r} — reorder the steps"
            )
        casts = step.get("value_type_casts")
        if casts is not None:
            if not isinstance(casts, dict) or not casts:
                raise ValueError(
                    f"step {step['step_name']!r}: value_type_casts must be a non-empty "
                    f"column → value_type map"
                )
            for column, value_type_name in casts.items():
                if not isinstance(column, str) or not column:
                    raise ValueError(f"step {step['step_name']!r}: value_type_casts keys must be column names")
                if not isinstance(value_type_name, str) or not value_type_name:
                    raise ValueError(
                        f"step {step['step_name']!r}: value_type_casts[{column!r}] must be a Value Type name"
                    )
        outputs_so_far.add(step["output_dataset"])


def _parse_pipeline_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result["steps"], str):
        result["steps"] = json.loads(result["steps"])
    return result


async def create_pipeline(pool: asyncpg.Pool, *, tenant_id: str, name: str, steps: list[dict]) -> dict:
    _validate_steps(steps)
    await pool.execute(
        """
        INSERT INTO pipeline_definition (tenant_id, name, steps)
        VALUES ($1, $2, $3::jsonb)
        ON CONFLICT (name) DO UPDATE SET steps = EXCLUDED.steps, updated_at = now()
        """,
        tenant_id, name, json.dumps(steps),
    )
    return await get_pipeline(pool, name)


async def get_pipeline(pool: asyncpg.Pool, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM pipeline_definition WHERE name = $1", name)
    return _parse_pipeline_row(row) if row else None


async def list_pipelines(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM pipeline_definition WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_pipeline_row(row) for row in rows]


async def delete_pipeline(pool: asyncpg.Pool, *, tenant_id: str, name: str) -> bool:
    """Remove definition + run history for this tenant. Returns False if missing."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            deleted = await conn.fetchrow(
                "DELETE FROM pipeline_definition WHERE name = $1 AND tenant_id = $2 RETURNING name",
                name,
                tenant_id,
            )
            if deleted is None:
                return False
            await conn.execute(
                "DELETE FROM pipeline_run WHERE pipeline_name = $1 AND tenant_id = $2",
                name,
                tenant_id,
            )
    return True


async def record_run(
    pool: asyncpg.Pool, *, tenant_id: str, pipeline_name: str, status: str, started_at, finished_at,
    step_results: list[dict], error: Optional[str] = None,
) -> dict:
    row = await pool.fetchrow(
        """
        INSERT INTO pipeline_run (tenant_id, pipeline_name, status, started_at, finished_at, step_results, error)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7)
        RETURNING *
        """,
        tenant_id, pipeline_name, status, started_at, finished_at, json.dumps(step_results), error,
    )
    result = dict(row)
    if isinstance(result["step_results"], str):
        result["step_results"] = json.loads(result["step_results"])
    return result


async def list_runs(pool: asyncpg.Pool, tenant_id: str, pipeline_name: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM pipeline_run WHERE tenant_id = $1 AND pipeline_name = $2 ORDER BY id DESC",
        tenant_id, pipeline_name,
    )
    results = []
    for row in rows:
        result = dict(row)
        if isinstance(result["step_results"], str):
            result["step_results"] = json.loads(result["step_results"])
        results.append(result)
    return results
