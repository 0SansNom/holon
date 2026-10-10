"""Pipeline transform runs for Connectivity."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from holon_common import EventActor, HolonError, Principal, build_urn, issue_token

from . import deps, iceberg_reader, iceberg_writer, pipeline
from .deps import (
    CONNECTOR_URN_PIPELINE,
    ICEBERG_CONFIG,
    JWT_ACTIVE_KID,
    JWT_SECRET,
    JWT_SECRETS,
    KNOWLEDGE_URL,
    PIPELINE_FUNCTION_CALLER_URN,
    TENANT_ID,
    WORKSPACE_ID,
)
from .ingest_sync import _finalize_sync

logger = logging.getLogger("connectivity.scheduler")


def _function_invocation_token() -> str:
    """Mint short-lived service token for internal function invocations."""
    principal = Principal(
        urn=PIPELINE_FUNCTION_CALLER_URN, type="service_account", tenant_id=TENANT_ID,
        display_name="Connectivity Pipeline Runner",
    )
    return issue_token(
        principal, JWT_SECRET, ttl_seconds=60, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS
    )

async def _latest_dataset_version_urn(dataset_name: str) -> Optional[str]:
    """Fetch latest dataset_version_urn from sync_run history."""
    dataset_urn = build_urn(TENANT_ID, WORKSPACE_ID, "dataset", dataset_name)
    row = await deps.pool.fetchrow(
        "SELECT dataset_version_urn FROM sync_run WHERE tenant_id = $1 AND dataset_urn = $2 ORDER BY id DESC LIMIT 1",
        TENANT_ID, dataset_urn,
    )
    return row["dataset_version_urn"] if row else None

async def _run_pipeline(name: str, *, actor: EventActor, tenant_id: str) -> dict:
    """Execute pipeline transform steps sequentially and finalize outputs."""
    definition = await pipeline.get_pipeline(deps.pool, tenant_id, name)
    if definition is None:
        raise HolonError.not_found('PipelineNotFound', f"unknown pipeline: {name}", name=name)

    run_started_at = datetime.now(timezone.utc)
    step_results: list[dict] = []

    try:
        for step in definition["steps"]:
            step_started_at = datetime.now(timezone.utc)
            source_dataset_version_urn = await _latest_dataset_version_urn(step["input_dataset"])

            input_rows = await asyncio.to_thread(
                iceberg_reader.read_table, step["input_dataset"], tenant_id=tenant_id, **ICEBERG_CONFIG
            )

            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{KNOWLEDGE_URL}/api/holon/functions/{step['function_name']}/invoke",
                    json={"rows": input_rows},
                    headers={"Authorization": f"Bearer {_function_invocation_token()}"},
                )
            response.raise_for_status()
            output_rows = response.json()["rows"]

            casts = step.get("value_type_casts") or {}
            if casts:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    cast_response = await client.post(
                        f"{KNOWLEDGE_URL}/value-types/validate-casts",
                        json={"casts": casts, "rows": output_rows},
                        headers={"Authorization": f"Bearer {_function_invocation_token()}"},
                    )
                cast_response.raise_for_status()
                cast_result = cast_response.json()
                if not cast_result.get("ok", False):
                    sample = cast_result.get("errors") or []
                    raise ValueError(
                        f"step {step['step_name']!r} value_type_casts failed "
                        f"({cast_result.get('error_count', len(sample))} errors): {sample[:3]}"
                    )

            write_result = await asyncio.to_thread(
                iceberg_writer.write_snapshot, output_rows, step["output_dataset"], tenant_id=tenant_id, **ICEBERG_CONFIG
            )
            step_finished_at = datetime.now(timezone.utc)

            sync_result = await _finalize_sync(
                connector_urn=CONNECTOR_URN_PIPELINE,
                dataset_name=step["output_dataset"],
                result=write_result,
                started_at=step_started_at,
                finished_at=step_finished_at,
                actor=actor,
                source_dataset_version_urn=source_dataset_version_urn,
                producer={
                    "kind": "pipeline",
                    "pipeline_name": name,
                    "step_name": step["step_name"],
                    "function_name": step["function_name"],
                },
            )
            step_results.append({"step_name": step["step_name"], **sync_result.model_dump()})
    except Exception as exc:
        run_finished_at = datetime.now(timezone.utc)
        await pipeline.record_run(
            deps.pool,
            tenant_id=tenant_id,
            pipeline_name=name,
            status="failed",
            started_at=run_started_at,
            finished_at=run_finished_at,
            step_results=step_results,
            error=str(exc),
        )
        raise HolonError.invalid_argument('PipelineRunFailed', f"pipeline run failed: {exc}") from exc

    run_finished_at = datetime.now(timezone.utc)
    return await pipeline.record_run(
        deps.pool,
        tenant_id=tenant_id,
        pipeline_name=name,
        status="succeeded",
        started_at=run_started_at,
        finished_at=run_finished_at,
        step_results=step_results,
    )

