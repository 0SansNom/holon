"""Pipeline / Transform DAG engine.

Manages pipeline definitions and step dependency validation for dataset transformations.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
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

-- Same scheduling model as generic_rest_source / plugin_registration — NULL = manual only.
ALTER TABLE pipeline_definition ADD COLUMN IF NOT EXISTS schedule_interval_minutes INTEGER;

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
    """Validate pipeline step definitions and DAG dependencies."""
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
            # Forward reference check: input dataset cannot depend on a later step's output
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


_HEALTH_JOIN = """
    LEFT JOIN LATERAL (
        SELECT status, started_at, finished_at, step_results, error
        FROM pipeline_run pr WHERE pr.pipeline_name = d.name ORDER BY pr.id DESC LIMIT 1
    ) lr ON true
    LEFT JOIN LATERAL (
        SELECT finished_at FROM pipeline_run pr2
        WHERE pr2.pipeline_name = d.name AND pr2.status = 'succeeded' ORDER BY pr2.id DESC LIMIT 1
    ) ls ON true
"""


def _parse_pipeline_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    if isinstance(result["steps"], str):
        result["steps"] = json.loads(result["steps"])
    return result


def _attach_health(result: dict) -> dict:
    """Attach health status metrics based on last run and last successful run."""
    step_results = result.pop("last_run_step_results", None)
    if isinstance(step_results, str):
        step_results = json.loads(step_results)
    last_success_at = result.pop("last_success_at", None)
    result["last_run"] = None
    if result.get("last_run_status") is not None:
        result["last_run"] = {
            "status": result.pop("last_run_status"),
            "started_at": result.pop("last_run_started_at"),
            "finished_at": result.pop("last_run_finished_at"),
            "error": result.pop("last_run_error"),
            "row_count": sum(step.get("row_count", 0) for step in (step_results or [])),
        }
    else:
        for key in ("last_run_status", "last_run_started_at", "last_run_finished_at", "last_run_error"):
            result.pop(key, None)
    result["last_success_at"] = last_success_at.isoformat() if last_success_at else None
    result["lag_seconds"] = (
        int((datetime.now(timezone.utc) - last_success_at).total_seconds()) if last_success_at else None
    )
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
    row = await pool.fetchrow(
        f"""
        SELECT d.*, lr.status AS last_run_status, lr.started_at AS last_run_started_at,
               lr.finished_at AS last_run_finished_at, lr.error AS last_run_error,
               lr.step_results AS last_run_step_results, ls.finished_at AS last_success_at
        FROM pipeline_definition d
        {_HEALTH_JOIN}
        WHERE d.name = $1
        """,
        name,
    )
    return _attach_health(_parse_pipeline_row(row)) if row else None


async def list_pipelines(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        f"""
        SELECT d.*, lr.status AS last_run_status, lr.started_at AS last_run_started_at,
               lr.finished_at AS last_run_finished_at, lr.error AS last_run_error,
               lr.step_results AS last_run_step_results, ls.finished_at AS last_success_at
        FROM pipeline_definition d
        {_HEALTH_JOIN}
        WHERE d.tenant_id = $1
        ORDER BY d.name
        """,
        tenant_id,
    )
    return [_attach_health(_parse_pipeline_row(row)) for row in rows]


async def set_pipeline_schedule(pool: asyncpg.Pool, name: str, schedule_interval_minutes: Optional[int]) -> Optional[dict]:
    await pool.execute(
        "UPDATE pipeline_definition SET schedule_interval_minutes = $1 WHERE name = $2",
        schedule_interval_minutes, name,
    )
    return await get_pipeline(pool, name)


async def list_all_scheduled_pipelines(pool: asyncpg.Pool) -> list[dict]:
    """Due-check feed for `main.py`'s scheduler loop — the pipeline
    counterpart of `generic_source_registry.list_all_scheduled_sources`
    and `plugin_registry.list_all_scheduled_plugins`.
    """
    rows = await pool.fetch(
        "SELECT tenant_id, name, schedule_interval_minutes FROM pipeline_definition "
        "WHERE schedule_interval_minutes IS NOT NULL"
    )
    return [dict(row) for row in rows]


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
