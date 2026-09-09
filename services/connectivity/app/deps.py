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


def _workspace_urn(tenant_id: str, workspace_id: str) -> str:
    return build_urn(tenant_id, "global", "workspace", workspace_id)


async def _authorize_workspace(
    principal: Principal, permission: str, *, workspace_id: Optional[str] = None
) -> None:
    """Authorize workspace permissions via ReBAC."""
    urn = _workspace_urn(principal.tenant_id, workspace_id or WORKSPACE_ID)
    decision = await authz.authorize(
        principal, resource_type="workspace", resource_urn=urn, permission=permission
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)
