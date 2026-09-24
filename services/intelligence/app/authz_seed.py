"""SpiceDB bootstrap for Intelligence sessions, tool plugins, and ML models.

Identity owns the SpiceDB schema; Intelligence links its own resources
under their workspace the same way Connectivity does for source/pipeline.
"""

from __future__ import annotations

import logging

import asyncpg

from holon_common.authz import PermissionClient

from .deps import TENANT_ID, ml_model_urn, tool_plugin_urn, workspace_urn

logger = logging.getLogger("intelligence.authz_seed")

_BACKFILL_KEY = "authz_backfill_v1"


async def seed_agent_session_parent_workspace(
    client: PermissionClient,
    *,
    tenant_id: str,
    session_urn: str,
) -> str:
    """Link an agent_session under its parent workspace. Returns the session URN."""
    await client.write_relationship(
        resource_type="agent_session",
        resource_urn=session_urn,
        relation="parent_workspace",
        subject_type="workspace",
        subject_urn=workspace_urn(tenant_id),
    )
    return session_urn


async def seed_tool_plugin_parent_workspace(
    client: PermissionClient,
    *,
    tenant_id: str,
    name: str,
) -> str:
    """Link a tool_plugin under its parent workspace. Returns the plugin URN."""
    urn = tool_plugin_urn(tenant_id, name)
    await client.write_relationship(
        resource_type="tool_plugin",
        resource_urn=urn,
        relation="parent_workspace",
        subject_type="workspace",
        subject_urn=workspace_urn(tenant_id),
    )
    return urn


async def seed_ml_model_parent_workspace(
    client: PermissionClient,
    *,
    tenant_id: str,
    name: str,
) -> str:
    """Link an ml_model under its parent workspace. Returns the model URN."""
    urn = ml_model_urn(tenant_id, name)
    await client.write_relationship(
        resource_type="ml_model",
        resource_urn=urn,
        relation="parent_workspace",
        subject_type="workspace",
        subject_urn=workspace_urn(tenant_id),
    )
    return urn


async def ensure_authz_seeded(client: PermissionClient, pool: asyncpg.Pool) -> None:
    """One-time backfill of parent_workspace for rows created before
    Intelligence wrote its own relationships. New rows are seeded inline.
    The done-flag is only set after a clean pass, so a partial failure is
    retried on the next boot without blocking this one."""
    if await pool.fetchval(
        "SELECT 1 FROM intelligence_runtime WHERE key = $1", _BACKFILL_KEY
    ):
        return

    sessions: list[dict] = []
    plugins: list[dict] = []
    models: list[dict] = []
    async with pool.acquire() as conn:
        for row in await conn.fetch("SELECT urn, tenant_id FROM agent_session"):
            sessions.append(dict(row))
        for row in await conn.fetch(
            "SELECT name FROM plugin_registration WHERE plugin_type = 'agent_tool'"
        ):
            plugins.append(dict(row))
        for row in await conn.fetch("SELECT name, tenant_id FROM model_registration"):
            models.append(dict(row))

    failed = 0
    total = len(sessions) + len(plugins) + len(models)

    for row in sessions:
        try:
            await seed_agent_session_parent_workspace(
                client, tenant_id=row["tenant_id"], session_urn=row["urn"]
            )
        except Exception:
            failed += 1
            logger.exception(
                "authz seed failed for agent_session urn=%s", row["urn"]
            )

    # Tool plugins are tenant-global registrations; seed under the process tenant.
    for row in plugins:
        try:
            await seed_tool_plugin_parent_workspace(
                client, tenant_id=TENANT_ID, name=row["name"]
            )
        except Exception:
            failed += 1
            logger.exception(
                "authz seed failed for tool_plugin name=%s", row["name"]
            )

    for row in models:
        try:
            await seed_ml_model_parent_workspace(
                client, tenant_id=row["tenant_id"], name=row["name"]
            )
        except Exception:
            failed += 1
            logger.exception(
                "authz seed failed for ml_model name=%s tenant=%s",
                row["name"],
                row["tenant_id"],
            )

    if failed:
        logger.error(
            "authz backfill: %d/%d writes failed; will retry next boot", failed, total
        )
        return

    await pool.execute(
        "INSERT INTO intelligence_runtime (key, value) VALUES ($1, 'done') "
        "ON CONFLICT (key) DO NOTHING",
        _BACKFILL_KEY,
    )
    logger.info("authz backfill seeded %d sessions/plugins/models", total)
