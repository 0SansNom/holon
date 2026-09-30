"""Experience shared helpers. `pool`, `authz`, `client`, and `breaker` are set by `main.py`'s lifespan."""
from __future__ import annotations

import json
import os
from typing import Any, Optional

import httpx
from fastapi import Request, Response

from holon_common import (
    CircuitBreakerOpenError,
    HolonError,
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

pool = None
authz = None
client = None
breaker = None

current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)

# SpiceDB type for a URN type segment. Sources and connections have no
# definition, so tagging them would check nothing.
_RESOURCE_AUTHZ_TYPE = {
    "object-type": "object_type",
    "application": "application",
}

_HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "host", "content-length", "date", "server"}


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
    """Bearer if present, otherwise the HttpOnly session cookie. The SPA sends no Authorization header."""
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
        # A plain-text upstream 500 must not raise while parsing JSON.
        return upstream.status_code, {"detail": upstream.text}


async def _post_json(url: str, *, authorization: Optional[str] = None, json: Optional[dict] = None) -> tuple[int, Any]:
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
        # A plain-text upstream 500 must not raise while parsing JSON.
        return upstream.status_code, {"detail": upstream.text}


async def _relay(base_url: str, path: str, request: Request) -> Response:
    target = f"{base_url}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    # Upstream hops read Authorization, not the HttpOnly session cookie.
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


def _upstream_detail(body: Any) -> str:
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


def _application_not_found(name: str) -> HolonError:
    return HolonError.not_found("ApplicationNotFound", f"no application named {name!r}", name=name)


def _resource_authz_type(urn: str) -> str:
    try:
        parsed = parse_urn(urn)
    except InvalidURNError as exc:
        raise HolonError.invalid_argument("SourceValidationFailed", str(exc)) from exc
    resource_type = _RESOURCE_AUTHZ_TYPE.get(parsed.type)
    if resource_type is None:
        raise HolonError.invalid_argument(
            "UnsupportedResourceType", f"tagging isn't supported for resource type {parsed.type!r}"
        )
    return resource_type


async def _authorize_resource(principal: Principal, resource_type: str, urn: str, permission: str) -> None:
    decision = await authz.authorize(
        principal, resource_type=resource_type, resource_urn=urn, permission=permission,
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_application(principal: Principal, urn: str, permission: str) -> None:
    await _authorize_resource(principal, "application", urn, permission)


async def _authorize_project(principal: Principal, project_urn: str, permission: str) -> None:
    await _authorize_resource(principal, "project", project_urn, permission)


async def _authorize_workspace(principal: Principal, permission: str) -> None:
    await _authorize_resource(principal, "workspace", WORKSPACE_URN, permission)


async def _link_application_to_project(application_urn: str, project_urn: Optional[str]) -> None:
    """SpiceDB TOUCH does not replace `parent_project`. Delete the old edge first, including when clearing it."""
    existing = await authz.read_relationships(
        resource_type="application", resource_urn=application_urn, relation="parent_project",
    )
    for relationship in existing:
        await authz.delete_relationship(
            resource_type="application",
            resource_urn=application_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=relationship["subject"]["object"]["objectId"],
        )
    if project_urn is not None:
        await authz.write_relationship(
            resource_type="application",
            resource_urn=application_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=project_urn,
        )


async def _filter_readable_resource_urns(principal: Principal, resource_urns: list[str]) -> list[str]:
    """An URN can embed a resource name. Unsupported members are skipped, not a 500."""
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


async def _get_application_or_404(name: str, principal: Principal, *, permission: str = "read") -> dict:
    """Knowledge authorizes the data. This guard is the application resource itself."""
    application = await application_builder.get_application(pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise _application_not_found(name)
    await _authorize_application(principal, application["urn"], permission)
    return application


def _json_response(body: Any, status_code: int) -> Response:
    return Response(content=json.dumps(body).encode(), status_code=status_code, media_type="application/json")
