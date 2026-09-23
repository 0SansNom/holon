"""Connectivity shared helpers and process-level runtime.

`pool` / `authz` / `kafka_stream_tasks` are set by `main.py`'s lifespan.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from pydantic import BaseModel

from holon_common import (
    HolonError,
    Principal,
    active_jwt,
    build_urn,
    make_principal_dependency,
)
from holon_common.spicedb_id import spicedb_object_id

from . import kafka_stream_registry

SERVICE_NAME = "connectivity-platform"
logger = logging.getLogger("connectivity.scheduler")

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()
DB_URL = os.environ["HOLON_DB_URL"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")
SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]

pool = None
authz = None
kafka_stream_tasks: dict = {}

ICEBERG_CONFIG = dict(
    catalog_uri=os.environ["HOLON_ICEBERG_CATALOG_URI"],
    warehouse=os.environ["HOLON_ICEBERG_WAREHOUSE"],
    s3_endpoint=os.environ["HOLON_S3_ENDPOINT"],
    access_key=os.environ["AWS_ACCESS_KEY_ID"],
    secret_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region=os.environ["AWS_REGION"],
)

CONNECTOR_URN_PIPELINE = build_urn(TENANT_ID, "global", "connector", "pipeline-transform")
STREAM_INGEST_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-stream-ingest")
SCHEDULER_ACTOR_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-scheduler")
SCHEDULER_POLL_SECONDS = 60
PIPELINE_FUNCTION_CALLER_URN = build_urn(TENANT_ID, "global", "service-account", "connectivity-pipeline-runner")
CLOSE_ACCOUNT_FAILURE_SENTINEL = "__simulate_failure__"
WORKFLOW_ENGINE_LOCAL_NAME = "automation-workflow-engine"
WORKFLOW_ENGINE_URN = build_urn(TENANT_ID, "global", "service-account", WORKFLOW_ENGINE_LOCAL_NAME)
_SCHEDULER_LOCK_KEY = 826_450_001

current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


def _source_db_url() -> str:
    url = (os.environ.get("HOLON_SOURCE_DB_URL") or "").strip()
    if not url:
        raise HolonError.unavailable("Unavailable", "HOLON_SOURCE_DB_URL is not configured")
    return url


async def _reserved_dataset_names(pool) -> frozenset[str]:
    """Returns dataset names owned by active Kafka streams."""
    active = await kafka_stream_registry.list_all_active(pool)
    return frozenset(source["dataset_name"] for source in active)


class SyncRequest(BaseModel):
    dataset: str = "customers"
    workspace_id: Optional[str] = None


class SyncResult(BaseModel):
    dataset_urn: str
    dataset_version_urn: str
    snapshot_id: int
    row_count: int
    location: str


def _resolve_workspace(
    *,
    explicit: Optional[str] = None,
    workspace_id: Optional[str] = None,
    x_holon_workspace_id: Optional[str] = None,
) -> str:
    return explicit or workspace_id or x_holon_workspace_id or WORKSPACE_ID


def workspace_urn(tenant_id: str, workspace_id: str) -> str:
    return build_urn(tenant_id, "global", "workspace", workspace_id)


def resource_workspace(row: dict) -> str:
    """Workspace of a source/pipeline row; legacy rows without one sit in the default."""
    return row.get("workspace_id") or WORKSPACE_ID


def source_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    return build_urn(tenant_id, workspace_id, "source", name)


def pipeline_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    return build_urn(tenant_id, workspace_id, "pipeline", name)


async def _authorize_workspace(
    principal: Principal, permission: str, *, workspace_id: Optional[str] = None
) -> None:
    """Authorize workspace permissions via ReBAC."""
    urn = workspace_urn(principal.tenant_id, workspace_id or WORKSPACE_ID)
    decision = await authz.authorize(
        principal, resource_type="workspace", resource_urn=urn, permission=permission
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_source(
    principal: Principal,
    permission: str,
    *,
    name: str,
    workspace_id: Optional[str] = None,
) -> None:
    """Authorize a source resource via ReBAC (inherits workspace grants)."""
    ws = workspace_id or WORKSPACE_ID
    urn = source_urn(principal.tenant_id, ws, name)
    decision = await authz.authorize(
        principal, resource_type="source", resource_urn=urn, permission=permission
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_pipeline(
    principal: Principal,
    permission: str,
    *,
    name: str,
    workspace_id: Optional[str] = None,
) -> None:
    """Authorize a pipeline resource via ReBAC (inherits workspace grants)."""
    ws = workspace_id or WORKSPACE_ID
    urn = pipeline_urn(principal.tenant_id, ws, name)
    decision = await authz.authorize(
        principal, resource_type="pipeline", resource_urn=urn, permission=permission
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _seed_source_authz(
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    compensate_delete,
) -> str:
    """Write parent_workspace for a source. `compensate_delete` undoes the
    Postgres insert on failure; pass None when the row predates this request."""
    from .authz_seed import seed_source_parent_workspace

    try:
        return await seed_source_parent_workspace(
            authz, tenant_id=tenant_id, workspace_id=workspace_id, name=name
        )
    except Exception as exc:
        if compensate_delete is not None:
            await compensate_delete()
        raise HolonError.unavailable(
            "AuthzSeedFailed",
            f"failed to seed SpiceDB relationship for source {name!r}: {exc}",
            name=name,
        ) from exc


async def _seed_pipeline_authz(
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    compensate_delete,
) -> str:
    """Write parent_workspace for a pipeline. `compensate_delete` undoes the
    Postgres insert on failure; pass None when the row predates this request."""
    from .authz_seed import seed_pipeline_parent_workspace

    try:
        return await seed_pipeline_parent_workspace(
            authz, tenant_id=tenant_id, workspace_id=workspace_id, name=name
        )
    except Exception as exc:
        if compensate_delete is not None:
            await compensate_delete()
        raise HolonError.unavailable(
            "AuthzSeedFailed",
            f"failed to seed SpiceDB relationship for pipeline {name!r}: {exc}",
            name=name,
        ) from exc



async def _unlink_resource_authz(
    resource_type: str, *, tenant_id: str, workspace_id: str, name: str
) -> None:
    """Drop parent_workspace after the Postgres row is gone, so a later
    resource with the same name in another workspace doesn't inherit it."""
    urn = build_urn(tenant_id, workspace_id, resource_type, name)
    try:
        await authz.delete_relationship(
            resource_type=resource_type,
            resource_urn=urn,
            relation="parent_workspace",
            subject_type="workspace",
            subject_urn=workspace_urn(tenant_id, workspace_id),
        )
    except Exception:
        logger.exception("SpiceDB parent_workspace cleanup failed for deleted %s %s", resource_type, urn)


async def _filter_readable(principal: Principal, resource_type: str, rows: list[dict]) -> list[dict]:
    """Keep rows the principal (and its mandant, if delegated) can `read`."""

    def _urn(row: dict) -> str:
        return build_urn(principal.tenant_id, resource_workspace(row), resource_type, row["name"])

    if not rows:
        return []
    try:
        readable = await authz.lookup_resource_ids(
            resource_type=resource_type, permission="read", principal_urn=principal.urn
        )
        if principal.on_behalf_of:
            readable &= await authz.lookup_resource_ids(
                resource_type=resource_type, permission="read", principal_urn=principal.on_behalf_of
            )
        return [row for row in rows if spicedb_object_id(_urn(row)) in readable]
    except Exception:
        logger.exception("%s LookupResources failed; falling back to per-row CheckPermission", resource_type)
        allowed = []
        for row in rows:
            urn = _urn(row)
            if not await authz.check_rebac(principal.urn, resource_type, urn, "read"):
                continue
            if principal.on_behalf_of and not await authz.check_rebac(
                principal.on_behalf_of, resource_type, urn, "read"
            ):
                continue
            allowed.append(row)
        return allowed
