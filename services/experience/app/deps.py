"""Experience shared helpers and process-level runtime.

`client`, `breaker`, `pool`, and `authz` are set once by `main.py`'s
lifespan. Route handlers live in `routers/` and must read them as
attributes of this module: a `from .deps import pool` binds None.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Request, Response

from holon_common import (
    HolonError,
    CircuitBreakerOpenError,
    InvalidURNError,
    Principal,
    active_jwt,
    build_urn,
    issue_token,
    make_principal_dependency,
    parse_urn,
)
from holon_common.auth import COOKIE_NAME

from . import application_builder

SERVICE_NAME = "experience-platform"

IDENTITY_URL = os.environ["HOLON_IDENTITY_URL"]
CONNECTIVITY_URL = os.environ["HOLON_CONNECTIVITY_URL"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
INTELLIGENCE_URL = os.environ["HOLON_INTELLIGENCE_URL"]
AUTOMATION_URL = os.environ.get("HOLON_AUTOMATION_URL", "")
TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()
DB_URL = os.environ["HOLON_DB_URL"]
KAFKA_BOOTSTRAP = os.environ["HOLON_KAFKA_BOOTSTRAP"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")

SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]

WORKSPACE_URN = build_urn(TENANT_ID, "global", "workspace", WORKSPACE_ID)

AGENT_URN = build_urn(TENANT_ID, "global", "agent", "ingest-bot")

_TIMEOUT_SECONDS = 5.0

STATIC_DIR = Path(__file__).parent / "static"

# Same objects as app.state.*; assigned in main.lifespan before serving.
client = None
breaker = None
pool = None
authz = None

current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


def _intelligence_enabled() -> bool:
    return os.environ.get("HOLON_INTELLIGENCE_ENABLED", "true").lower() in {"1", "true", "yes"}


def _agent_app_session_token(on_behalf_of_urn: str) -> str:
    principal = Principal(
        urn=AGENT_URN, type="agent", tenant_id=TENANT_ID, display_name="Ingest Bot", on_behalf_of=on_behalf_of_urn,
    )
    return issue_token(
        principal, JWT_SECRET, ttl_seconds=300, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS
    )


def _upstream_authorization(request: Request) -> Optional[str]:
    """Forward the caller's JWT to Knowledge/Intelligence.

    HTTP tests mint a Bearer token. The SPA only has the HttpOnly
    `holon_session` cookie — without this rewrite, Application surfaces
    hit Knowledge unauthenticated while Object Explorer (generic `_relay`)
    still works.
    """
    authorization = request.headers.get("authorization")
    if authorization and authorization.lower().startswith("bearer "):
        return authorization
    cookie = request.cookies.get(COOKIE_NAME)
    if cookie:
        return f"Bearer {cookie}"
    return None


async def _proxy(method: str, url: str, *, authorization: Optional[str] = None, json: Optional[dict] = None) -> Response:
    headers = {"Authorization": authorization} if authorization else {}

    async def _do() -> httpx.Response:
        return await client.request(method, url, headers=headers, json=json)

    try:
        upstream = await breaker.call(_do)
    except CircuitBreakerOpenError:
        return Response(
            content=b'{"detail": "upstream temporarily unavailable"}', status_code=503, media_type="application/json"
        )
    return Response(content=upstream.content, status_code=upstream.status_code, media_type="application/json")


async def _get_json(url: str, *, authorization: Optional[str] = None) -> tuple[int, Any]:
    """Same breaker/timeout discipline as `_proxy`, but returns the
    parsed body instead of a raw `Response` — needed wherever this
    service has to actually read the upstream data (the dashboard
    surface computing a `kpi` count), not just relay it byte-for-byte.
    """
    headers = {"Authorization": authorization} if authorization else {}

    async def _do() -> httpx.Response:
        return await client.get(url, headers=headers)

    try:
        upstream = await breaker.call(_do)
    except CircuitBreakerOpenError:
        return 503, {"detail": "upstream temporarily unavailable"}
    try:
        return upstream.status_code, upstream.json()
    except ValueError:
        # An upstream error response isn't guaranteed to be JSON (e.g. a
        # plain-text 500 from an unhandled exception) — parsing it as
        # JSON must not itself become an unhandled crash here.
        return upstream.status_code, {"detail": upstream.text}


async def _post_json(url: str, *, authorization: Optional[str] = None, json: Optional[dict] = None) -> tuple[int, Any]:
    """`_get_json`'s POST counterpart — needed wherever this service must
    read the upstream *response body* itself rather than just relay it
    (platform's agent-session creation needs the new session's own `urn`
    back, to record who's allowed to drive it).
    """
    headers = {"Authorization": authorization} if authorization else {}

    async def _do() -> httpx.Response:
        return await client.post(url, headers=headers, json=json)

    try:
        upstream = await breaker.call(_do)
    except CircuitBreakerOpenError:
        return 503, {"detail": "upstream temporarily unavailable"}
    try:
        return upstream.status_code, upstream.json()
    except ValueError:
        # An upstream error response isn't guaranteed to be JSON (e.g. a
        # plain-text 500 from an unhandled exception) — parsing it as
        # JSON must not itself become an unhandled crash here.
        return upstream.status_code, {"detail": upstream.text}


# x-correlation-id: the validated id is re-added by holon_common.correlation, not copied from either side.
_HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding", "host", "content-length", "date", "server", "x-correlation-id",
}


async def _relay(base_url: str, path: str, request: Request) -> Response:
    target = f"{base_url}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    # SPA sessions are cookie-only. Upstream services that re-forward the
    # caller's JWT (Intelligence → Knowledge for GET /tools) read
    # Authorization, not our HttpOnly cookie — rewrite here so those
    # hops stay authenticated (same as _proxy / agent-sessions).
    auth = _upstream_authorization(request)
    if auth:
        headers["authorization"] = auth
    body = await request.body()

    async def _do() -> httpx.Response:
        return await client.request(
            request.method, target, params=request.query_params,
            headers=headers, content=body, follow_redirects=False,
        )

    try:
        upstream = await breaker.call(_do)
    except CircuitBreakerOpenError:
        return Response(
            content=b'{"detail": "upstream temporarily unavailable"}', status_code=503, media_type="application/json"
        )
    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


async def _authorize_resource(principal: Principal, resource_type: str, urn: str, permission: str) -> None:
    decision = await authz.authorize(
        principal, resource_type=resource_type, resource_urn=urn, permission=permission,
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_application(principal: Principal, urn: str, permission: str) -> None:
    await _authorize_resource(principal, "application", urn, permission)


async def _link_application_to_project(application_urn: str, project_urn: Optional[str]) -> None:
    """Postgres's `application.project_urn` is single-valued; mirror it in SpiceDB.

    `set_single_subject` deletes any existing `parent_project` edge in the
    same write: SpiceDB `OPERATION_TOUCH` does not replace a relationship.
    """
    await authz.set_single_subject(
        resource_type="application",
        resource_urn=application_urn,
        relation="parent_project",
        subject_type="project",
        subject_urn=project_urn,
    )


# Resource tags/featured (`/api/resources/*`): which SpiceDB `resource_type`
# a URN's `hl:{tenant}:{workspace}:{type}:{id}` `type` segment maps to. Only
# resource kinds with real ReBAC enforcement can be tagged — deliberately not
# every URN type that merely exists (Sources/Connections have no SpiceDB
# definition yet; tagging them would have nothing real to check).
_RESOURCE_AUTHZ_TYPE = {
    "object-type": "object_type",
    "application": "application",
}


def _resource_authz_type(urn: str) -> str:
    try:
        parsed = parse_urn(urn)
    except InvalidURNError as exc:
        raise HolonError.invalid_argument('SourceValidationFailed', str(exc)) from exc
    resource_type = _RESOURCE_AUTHZ_TYPE.get(parsed.type)
    if resource_type is None:
        raise HolonError.invalid_argument('UnsupportedResourceType', f"tagging isn't supported for resource type {parsed.type!r}")
    return resource_type


async def _filter_readable_resource_urns(principal: Principal, resource_urns: list[str]) -> list[str]:
    """Return only resource references the principal may read.

    Collections are workspace-level containers, but their members can point
    to independently governed resources.  An URN is metadata that can itself
    reveal a sensitive resource name, so it receives the same per-resource
    filtering as ``GET /api/resources``.  Invalid or unsupported legacy
    members are withheld rather than turning a read into a 500.
    """
    readable = []
    for resource_urn in resource_urns:
        try:
            resource_type = _resource_authz_type(resource_urn)
        except HolonError:
            continue
        decision = await authz.authorize(
            principal, resource_type=resource_type, resource_urn=resource_urn, permission="read",
        )
        if decision.allowed:
            readable.append(resource_urn)
    return readable


async def _authorize_workspace(principal: Principal, permission: str) -> None:
    await _authorize_resource(principal, "workspace", WORKSPACE_URN, permission)


def _application_not_found(name: str) -> HolonError:
    return HolonError.not_found("ApplicationNotFound", f"no application named {name!r}", name=name)


async def _get_application_or_404(name: str, principal: Principal, *, permission: str = "read") -> dict:
    """Load an application only after enforcing its own ReBAC permission.

    Knowledge separately authorizes the data and actions behind an
    application.  This guard protects the application resource itself:
    its routes, form schema, dashboard layout, and agent configuration.
    """
    application = await application_builder.get_application(pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise _application_not_found(name)
    await _authorize_application(principal, application["urn"], permission)
    return application


def _upstream_detail(body: Any) -> str:
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)
