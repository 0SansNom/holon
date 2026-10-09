"""Automation shared helpers and process-level runtime.

`pool` / `authz` are set by `main.py`'s lifespan.
Route handlers live in `routers/`.
"""

from __future__ import annotations

import os

from holon_common import HolonError, Principal, active_jwt, build_urn, make_principal_dependency

SERVICE_NAME = "automation-platform"

TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()
DB_URL = os.environ["HOLON_DB_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
CONNECTIVITY_URL = os.environ["HOLON_CONNECTIVITY_URL"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
INTELLIGENCE_URL = os.environ["HOLON_INTELLIGENCE_URL"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")
SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]

pool = None
authz = None

current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


def workspace_urn(tenant_id: str, workspace_id: str | None = None) -> str:
    return build_urn(tenant_id, "global", "workspace", workspace_id or WORKSPACE_ID)


async def authorize_workspace(principal: Principal, permission: str) -> None:
    decision = await authz.authorize(
        principal,
        resource_type="workspace",
        resource_urn=workspace_urn(principal.tenant_id),
        permission=permission,
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)
