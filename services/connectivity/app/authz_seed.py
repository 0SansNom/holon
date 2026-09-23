"""SpiceDB bootstrap for Connectivity sources and pipelines.

Identity owns the SpiceDB schema; Connectivity links its own resources
under their workspace the same way Knowledge does for object_type.
"""

from __future__ import annotations

import logging

import asyncpg

from holon_common.authz import PermissionClient

from .deps import pipeline_urn, resource_workspace, source_urn, workspace_urn

logger = logging.getLogger("connectivity.authz_seed")


async def seed_source_parent_workspace(
    client: PermissionClient,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
) -> str:
    """Link a source under its parent workspace. Returns the source URN."""
    urn = source_urn(tenant_id, workspace_id, name)
    await client.write_relationship(
        resource_type="source",
        resource_urn=urn,
        relation="parent_workspace",
        subject_type="workspace",
        subject_urn=workspace_urn(tenant_id, workspace_id),
    )
    return urn


async def seed_pipeline_parent_workspace(
    client: PermissionClient,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
) -> str:
    """Link a pipeline under its parent workspace. Returns the pipeline URN."""
    urn = pipeline_urn(tenant_id, workspace_id, name)
    await client.write_relationship(
        resource_type="pipeline",
        resource_urn=urn,
        relation="parent_workspace",
        subject_type="workspace",
        subject_urn=workspace_urn(tenant_id, workspace_id),
    )
    return urn


_BACKFILL_KEY = "authz_backfill_v1"


async def ensure_authz_seeded(client: PermissionClient, pool: asyncpg.Pool) -> None:
    """One-time backfill of parent_workspace for rows created before
    Connectivity wrote its own relationships. New rows are seeded inline.
    The done-flag is only set after a clean pass, so a partial failure is
    retried on the next boot without blocking this one."""
    if await pool.fetchval("SELECT 1 FROM connectivity_runtime WHERE key = $1", _BACKFILL_KEY):
        return

    rows: list[tuple[str, dict]] = []
    async with pool.acquire() as conn:
        for kind, table in (
            ("source", "generic_rest_source"),
            ("source", "sql_source"),
            ("source", "object_source"),
            ("source", "sftp_source"),
            ("pipeline", "pipeline_definition"),
        ):
            for row in await conn.fetch(f"SELECT tenant_id, name, workspace_id FROM {table}"):
                rows.append((kind, dict(row)))

    seed = {"source": seed_source_parent_workspace, "pipeline": seed_pipeline_parent_workspace}
    failed = 0
    for kind, row in rows:
        workspace_id = resource_workspace(row)
        try:
            await seed[kind](
                client, tenant_id=row["tenant_id"], workspace_id=workspace_id, name=row["name"]
            )
        except Exception:
            failed += 1
            logger.exception(
                "authz seed failed for %s tenant=%s workspace=%s name=%s",
                kind, row["tenant_id"], workspace_id, row["name"],
            )
    if failed:
        logger.error("authz backfill: %d/%d writes failed; will retry next boot", failed, len(rows))
        return

    await pool.execute(
        "INSERT INTO connectivity_runtime (key, value) VALUES ($1, 'done') ON CONFLICT (key) DO NOTHING",
        _BACKFILL_KEY,
    )
    logger.info("authz backfill seeded %d sources/pipelines", len(rows))
