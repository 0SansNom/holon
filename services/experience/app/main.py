"""Experience Platform — Serves the React SPA and Application Builder API."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from holon_common import (
    HolonError,
    CircuitBreaker,
    CircuitBreakerOpenError,
    InvalidURNError,
    Principal,
    active_jwt,
    assert_production_posture,
    build_urn,
    configure_json_logging,
    create_pool,
    install_error_handlers,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    issue_token,
    make_principal_dependency,
    parse_urn,
    retry_with_backoff,
    run_migrations,
    is_production,
)
from holon_common.principal_status import (
    consume_identity_auth_events,
    hydrate_revocation_snapshot,
    make_principal_status_consumer,
)
from holon_common.audit import clear_durable_audit_hooks, emit_audit
from holon_common.audit_store import install_durable_audit, list_events_page
from holon_common.auth import COOKIE_NAME
from holon_common.authz import PermissionClient
from holon_common.readiness import check_kafka_bootstrap, check_opa, check_postgres, check_spicedb, report_ready

from . import application_builder, project_pins, resource_tags, ui_component_registry
from . import collections as resource_collections

SERVICE_NAME = "experience-platform"
configure_json_logging(SERVICE_NAME)

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
SPICEDB_SCHEMA_PATH = os.environ["HOLON_SPICEDB_SCHEMA_PATH"]

WORKSPACE_URN = build_urn(TENANT_ID, "global", "workspace", WORKSPACE_ID)

AGENT_URN = build_urn(TENANT_ID, "global", "agent", "ingest-bot")


def _intelligence_enabled() -> bool:
    return os.environ.get("HOLON_INTELLIGENCE_ENABLED", "true").lower() in {"1", "true", "yes"}


def _agent_app_session_token(on_behalf_of_urn: str) -> str:
    principal = Principal(
        urn=AGENT_URN, type="agent", tenant_id=TENANT_ID, display_name="Ingest Bot", on_behalf_of=on_behalf_of_urn,
    )
    return issue_token(
        principal, JWT_SECRET, ttl_seconds=300, kid=JWT_ACTIVE_KID, secrets=JWT_SECRETS
    )

STATIC_DIR = Path(__file__).parent / "static"

_TIMEOUT_SECONDS = 5.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    assert_production_posture(service_name=SERVICE_NAME)
    app.state.client = httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    )
    app.state.breaker = CircuitBreaker(name="experience-proxy", failure_threshold=5, cooldown_seconds=30.0)

    app.state.pool = await create_pool(DB_URL)
    # Experience-owned tables live in app/migrations/0000_baseline.sql.
    await run_migrations(app.state.pool, Path(__file__).parent / "migrations")

    clear_durable_audit_hooks()
    install_durable_audit(app.state.pool)

    app.state.authz = PermissionClient(SPICEDB_URL, SPICEDB_PRESHARED_KEY, OPA_URL)

    async def _seed_application_authz() -> None:
        await app.state.authz.write_schema(Path(SPICEDB_SCHEMA_PATH).read_text())
        backfilled = await application_builder.backfill_urns(
            app.state.pool, tenant_id=TENANT_ID, workspace_id=WORKSPACE_ID,
        )
        for name in backfilled:
            await app.state.authz.write_relationship(
                resource_type="application",
                resource_urn=application_builder.application_urn(TENANT_ID, WORKSPACE_ID, name),
                relation="parent_workspace",
                subject_type="workspace",
                subject_urn=WORKSPACE_URN,
            )

    await retry_with_backoff(_seed_application_authz, what="experience authz seed")

    status_consumer = make_principal_status_consumer(KAFKA_BOOTSTRAP, service_name=SERVICE_NAME)
    status_task = asyncio.create_task(consume_identity_auth_events(status_consumer, authz=app.state.authz))
    await retry_with_backoff(hydrate_revocation_snapshot, what="identity revocation snapshot")

    yield
    status_task.cancel()
    await status_consumer.stop()
    await app.state.pool.close()
    await app.state.client.aclose()


app = FastAPI(title="Holon — Experience Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)
current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


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
        return await app.state.client.request(method, url, headers=headers, json=json)

    try:
        upstream = await app.state.breaker.call(_do)
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
        return await app.state.client.get(url, headers=headers)

    try:
        upstream = await app.state.breaker.call(_do)
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
        return await app.state.client.post(url, headers=headers, json=json)

    try:
        upstream = await app.state.breaker.call(_do)
    except CircuitBreakerOpenError:
        return 503, {"detail": "upstream temporarily unavailable"}
    try:
        return upstream.status_code, upstream.json()
    except ValueError:
        # An upstream error response isn't guaranteed to be JSON (e.g. a
        # plain-text 500 from an unhandled exception) — parsing it as
        # JSON must not itself become an unhandled crash here.
        return upstream.status_code, {"detail": upstream.text}


_HOP_BY_HOP = {"connection", "keep-alive", "transfer-encoding", "host", "content-length", "date", "server"}


async def _relay(base_url: str, path: str, request: Request) -> Response:
    target = f"{base_url}/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP}
    body = await request.body()

    async def _do() -> httpx.Response:
        return await app.state.client.request(
            request.method, target, params=request.query_params,
            headers=headers, content=body, follow_redirects=False,
        )

    try:
        upstream = await app.state.breaker.call(_do)
    except CircuitBreakerOpenError:
        return Response(
            content=b'{"detail": "upstream temporarily unavailable"}', status_code=503, media_type="application/json"
        )
    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP_BY_HOP}
    return Response(content=upstream.content, status_code=upstream.status_code, headers=response_headers)


@app.api_route("/api/identity/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_identity(path: str, request: Request) -> Response:
    return await _relay(IDENTITY_URL, path, request)


@app.api_route("/api/connectivity/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_connectivity(
    path: str, request: Request, _: Principal = Depends(current_principal)
) -> Response:
    return await _relay(CONNECTIVITY_URL, path, request)


@app.api_route("/api/knowledge/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_knowledge(
    path: str, request: Request, _: Principal = Depends(current_principal)
) -> Response:
    return await _relay(KNOWLEDGE_URL, path, request)


@app.api_route("/api/intelligence/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def proxy_intelligence(
    path: str, request: Request, _: Principal = Depends(current_principal)
) -> Response:
    return await _relay(INTELLIGENCE_URL, path, request)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    return await report_ready(
        [
            check_postgres(app.state.pool),
            check_spicedb(SPICEDB_URL, SPICEDB_PRESHARED_KEY),
            check_opa(OPA_URL),
            check_kafka_bootstrap(KAFKA_BOOTSTRAP),
        ]
    )


@app.get("/api/config")
async def config() -> dict:
    """Public bootstrap flags. No demo principal or ObjectType — the
    instance may be empty (ADR 026) and login is Identity's job.
    """
    return {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "intelligence_enabled": _intelligence_enabled(),
        "require_connector_secret_ref": is_production(),
    }


@app.get("/api/audit-events")
async def list_experience_audit_events(
    principal: Principal = Depends(current_principal),
    category: Optional[str] = None,
    action: Optional[str] = None,
    actor: Optional[str] = None,
    outcome: Optional[str] = None,
    pageSize: Optional[int] = None,
    pageToken: Optional[str] = None,
) -> dict:
    """Durable Experience audit (applications, collections, UI plugins)."""
    await _authorize_workspace(principal, "approve")
    return await list_events_page(
        app.state.pool,
        principal.tenant_id,
        category=category,
        action=action,
        actor_urn=actor,
        outcome=outcome,
        page_size=50 if pageSize is None else pageSize,
        page_token=pageToken,
    )


@app.get("/api/lineage/{urn:path}")
async def get_lineage(
    urn: str, request: Request, _: Principal = Depends(current_principal)
) -> Response:
    return await _proxy(
        "GET",
        f"{KNOWLEDGE_URL}/api/holon/lineage/{urn}",
        authorization=_upstream_authorization(request),
    )


class ApplicationDefinitionRequest(BaseModel):
    definition: dict[str, Any]


def _application_not_found(name: str) -> HolonError:
    return HolonError.not_found("ApplicationNotFound", f"no application named {name!r}", name=name)


async def _authorize_resource(principal: Principal, resource_type: str, urn: str, permission: str) -> None:
    decision = await app.state.authz.authorize(
        principal, resource_type=resource_type, resource_urn=urn, permission=permission,
    )
    if not decision.allowed:
        raise HolonError.forbidden("PermissionDenied", decision.reason)


async def _authorize_application(principal: Principal, urn: str, permission: str) -> None:
    await _authorize_resource(principal, "application", urn, permission)


async def _link_application_to_project(application_urn: str, project_urn: Optional[str]) -> None:
    """Same reconciliation `knowledge`'s `_link_object_type_to_project`
    already does: SpiceDB relationships are additive (`OPERATION_TOUCH`),
    so re-scoping — or clearing back to `None` — must delete any existing
    `parent_project` edge first, since Postgres's `application.project_urn`
    is single-valued but SpiceDB wouldn't otherwise know the old edge is
    stale.
    """
    existing = await app.state.authz.read_relationships(
        resource_type="application", resource_urn=application_urn, relation="parent_project",
    )
    for relationship in existing:
        await app.state.authz.delete_relationship(
            resource_type="application",
            resource_urn=application_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=relationship["subject"]["object"]["objectId"],
        )
    if project_urn is not None:
        await app.state.authz.write_relationship(
            resource_type="application",
            resource_urn=application_urn,
            relation="parent_project",
            subject_type="project",
            subject_urn=project_urn,
        )


# Resource tags/featured (`/api/resources/*` below): which SpiceDB
# `resource_type` a URN's `hl:{tenant}:{workspace}:{type}:{id}` `type`
# segment maps to. Only resource kinds with real ReBAC enforcement can be
# tagged — deliberately not every URN type that merely *exists*
# (Sources/Connections have no SpiceDB definition yet, see the plan's
# "explicitly deferred" — tagging them would have nothing real to check).
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
        decision = await app.state.authz.authorize(
            principal, resource_type=resource_type, resource_urn=resource_urn, permission="read",
        )
        if decision.allowed:
            readable.append(resource_urn)
    return readable


@app.post("/api/applications/{name}")
async def create_or_update_application(
    name: str,
    body: ApplicationDefinitionRequest,
    http_request: Request,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Idempotent on an unpromoted draft (edits it in
    place); creates a new draft version if the latest is promoted.
    """
    urn = application_builder.application_urn(principal.tenant_id, WORKSPACE_ID, name)
    existing = await application_builder.get_application(app.state.pool, tenant_id=principal.tenant_id, name=name)
    if existing is not None:
        await _authorize_application(principal, urn, "write")
    else:
        decision = await app.state.authz.authorize(
            principal, resource_type="workspace", resource_urn=WORKSPACE_URN, permission="write",
        )
        if not decision.allowed:
            raise HolonError.forbidden("PermissionDenied", decision.reason)

    authorization = _upstream_authorization(http_request) or ""
    try:
        result = await application_builder.create_or_update_draft(
            app.state.pool,
            app.state.client,
            tenant_id=principal.tenant_id,
            workspace_id=WORKSPACE_ID,
            name=name,
            definition=body.definition,
            knowledge_url=KNOWLEDGE_URL,
            intelligence_url=INTELLIGENCE_URL,
            authorization=authorization,
        )
    except application_builder.InvalidApplicationDefinition as exc:
        raise HolonError.invalid_argument('ExperienceValidationFailed', str(exc)) from exc

    if existing is None:
        await app.state.authz.write_relationship(
            resource_type="application", resource_urn=urn, relation="parent_workspace",
            subject_type="workspace", subject_urn=WORKSPACE_URN,
        )
    emit_audit(
        category="access",
        action="experience.application.saved",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="application",
        resource_urn=urn,
        extra={"created": existing is None, "name": name},
    )
    return result


@app.get("/api/applications")
async def list_applications(principal: Principal = Depends(current_principal)) -> list[dict]:
    """A real, previously-missing gap — every prior verification already
    knew the Application's name from creating it. Returns the latest
    version of each distinct application for this tenant.

    Filtered post-fetch rather than per-row-authorized: `authorize()`'s own
    decision cache makes repeat checks for the same principal+permission
    cheap, and the list is small at this build's scale (unlike a paged
    object table, where per-row authz would actually matter).
    """
    applications = await application_builder.list_applications(app.state.pool, tenant_id=principal.tenant_id)
    allowed = []
    for application in applications:
        decision = await app.state.authz.authorize(
            principal, resource_type="application", resource_urn=application["urn"], permission="read",
        )
        if decision.allowed:
            allowed.append(application)
    return allowed


@app.get("/api/applications/{name}")
async def get_application(name: str, principal: Principal = Depends(current_principal)) -> dict:
    application = await application_builder.get_application(app.state.pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise _application_not_found(name)
    await _authorize_application(principal, application["urn"], "read")
    return application


class SetApplicationProjectRequest(BaseModel):
    project_urn: Optional[str] = None


@app.post("/api/applications/{name}/project")
async def set_application_project(
    name: str, body: SetApplicationProjectRequest, principal: Principal = Depends(current_principal),
) -> dict:
    application = await application_builder.get_application(app.state.pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise _application_not_found(name)
    # Same bar as any other edit to this Application — no separate
    # project-level check, mirroring how `knowledge`'s ObjectType project
    # scoping is gated by the object_type's own `write`, not the target
    # project's (the resource owner's call to make, not the project's to
    # approve).
    await _authorize_application(principal, application["urn"], "write")
    await _link_application_to_project(application["urn"], body.project_urn)
    return await application_builder.set_application_project(
        app.state.pool, tenant_id=principal.tenant_id, name=name, project_urn=body.project_urn,
    )


class SetTagsRequest(BaseModel):
    tags: list[str]


@app.put("/api/resources/{urn}/tags")
async def set_resource_tags(
    urn: str, body: SetTagsRequest, principal: Principal = Depends(current_principal)
) -> dict:
    await _authorize_resource(principal, _resource_authz_type(urn), urn, "write")
    return await resource_tags.set_tags(
        app.state.pool, tenant_id=principal.tenant_id, resource_urn=urn, tags=body.tags, updated_by_urn=principal.urn,
    )


@app.post("/api/resources/{urn}/featured")
async def feature_resource(urn: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_resource(principal, _resource_authz_type(urn), urn, "write")
    return await resource_tags.set_featured(
        app.state.pool, tenant_id=principal.tenant_id, resource_urn=urn, featured=True, updated_by_urn=principal.urn,
    )


@app.delete("/api/resources/{urn}/featured")
async def unfeature_resource(urn: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_resource(principal, _resource_authz_type(urn), urn, "write")
    return await resource_tags.set_featured(
        app.state.pool, tenant_id=principal.tenant_id, resource_urn=urn, featured=False, updated_by_urn=principal.urn,
    )


@app.get("/api/resources")
async def list_resources(
    tag: Optional[str] = None, featured: Optional[bool] = None, principal: Principal = Depends(current_principal),
) -> list[dict]:
    """Per-row filtered, same discipline `list_applications` already
    applies — a URN itself can be sensitive (e.g. embeds a resource
    name), so a tag/featured search must not surface one this principal
    couldn't otherwise read, even though this endpoint returns no
    resource content beyond the URN itself.
    """
    candidates = await resource_tags.list_matching(app.state.pool, tenant_id=principal.tenant_id, tag=tag, featured=featured)
    allowed = []
    for candidate in candidates:
        resource_type = _RESOURCE_AUTHZ_TYPE.get(parse_urn(candidate["resource_urn"]).type)
        if resource_type is None:
            continue
        decision = await app.state.authz.authorize(
            principal, resource_type=resource_type, resource_urn=candidate["resource_urn"], permission="read",
        )
        if decision.allowed:
            allowed.append(candidate)
    return allowed


async def _authorize_project(principal: Principal, project_urn: str, permission: str) -> None:
    await _authorize_resource(principal, "project", project_urn, permission)


@app.post("/api/projects/{project_urn}/pins/{resource_urn}")
async def pin_resource(project_urn: str, resource_urn: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_project(principal, project_urn, "write")
    await project_pins.pin(
        app.state.pool, tenant_id=principal.tenant_id, project_urn=project_urn, resource_urn=resource_urn,
        pinned_by_urn=principal.urn,
    )
    return {"status": "pinned", "project_urn": project_urn, "resource_urn": resource_urn}


@app.delete("/api/projects/{project_urn}/pins/{resource_urn}")
async def unpin_resource(project_urn: str, resource_urn: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_project(principal, project_urn, "write")
    await project_pins.unpin(
        app.state.pool, tenant_id=principal.tenant_id, project_urn=project_urn, resource_urn=resource_urn,
    )
    return {"status": "unpinned", "project_urn": project_urn, "resource_urn": resource_urn}


@app.get("/api/projects/{project_urn}/pins")
async def list_project_pins(project_urn: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    # Read-gated, not write — a project viewer should still see what's
    # pinned, only curating (pin/unpin above) needs write. Member URNs
    # are filtered the same way collection members are: a pin must not
    # disclose a resource this principal cannot read.
    await _authorize_project(principal, project_urn, "read")
    pins = await project_pins.list_pins(app.state.pool, tenant_id=principal.tenant_id, project_urn=project_urn)
    readable = set(
        await _filter_readable_resource_urns(principal, [pin["resource_urn"] for pin in pins])
    )
    return [pin for pin in pins if pin["resource_urn"] in readable]


async def _authorize_workspace(principal: Principal, permission: str) -> None:
    await _authorize_resource(principal, "workspace", WORKSPACE_URN, permission)


def _collection_not_found(collection_id: int) -> HolonError:
    return HolonError.not_found(
        "CollectionNotFound", f"no collection with id {collection_id}", collection_id=collection_id
    )


class CreateCollectionRequest(BaseModel):
    name: str
    description: str = ""


@app.post("/api/collections")
async def create_collection(body: CreateCollectionRequest, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    if await resource_collections.get_collection_by_name(app.state.pool, tenant_id=principal.tenant_id, name=body.name):
        raise HolonError.conflict('CollectionAlreadyExists', f"a collection named {body.name!r} already exists")
    return await resource_collections.create_collection(
        app.state.pool, tenant_id=principal.tenant_id, name=body.name, description=body.description,
        created_by_urn=principal.urn,
    )


@app.get("/api/collections")
async def list_collections(principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    return await resource_collections.list_collections(app.state.pool, tenant_id=principal.tenant_id)


@app.get("/api/collections/{collection_id}")
async def get_collection(collection_id: int, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    collection = await resource_collections.get_collection(app.state.pool, tenant_id=principal.tenant_id, collection_id=collection_id)
    if collection is None:
        raise _collection_not_found(collection_id)
    members = await resource_collections.list_members(
        app.state.pool, tenant_id=principal.tenant_id, collection_id=collection_id,
    )
    collection["members"] = await _filter_readable_resource_urns(principal, members)
    return collection


@app.delete("/api/collections/{collection_id}")
async def delete_collection(collection_id: int, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    await resource_collections.delete_collection(app.state.pool, tenant_id=principal.tenant_id, collection_id=collection_id)
    return {"status": "deleted", "id": collection_id}


class SetCollectionMembersRequest(BaseModel):
    resource_urns: list[str]


@app.put("/api/collections/{collection_id}/members")
async def set_collection_members(
    collection_id: int, body: SetCollectionMembersRequest, principal: Principal = Depends(current_principal),
) -> dict:
    await _authorize_workspace(principal, "write")
    if await resource_collections.get_collection(app.state.pool, tenant_id=principal.tenant_id, collection_id=collection_id) is None:
        raise _collection_not_found(collection_id)
    members = await resource_collections.set_members(
        app.state.pool, tenant_id=principal.tenant_id, collection_id=collection_id,
        resource_urns=body.resource_urns, added_by_urn=principal.urn,
    )
    return {"id": collection_id, "members": members}


@app.post("/api/collections/{collection_id}/members/{resource_urn}")
async def add_collection_member(
    collection_id: int, resource_urn: str, principal: Principal = Depends(current_principal),
) -> dict:
    await _authorize_workspace(principal, "write")
    if await resource_collections.get_collection(app.state.pool, tenant_id=principal.tenant_id, collection_id=collection_id) is None:
        raise _collection_not_found(collection_id)
    await resource_collections.add_member(
        app.state.pool, tenant_id=principal.tenant_id, collection_id=collection_id, resource_urn=resource_urn,
        added_by_urn=principal.urn,
    )
    return {"status": "added"}


@app.delete("/api/collections/{collection_id}/members/{resource_urn}")
async def remove_collection_member(
    collection_id: int, resource_urn: str, principal: Principal = Depends(current_principal),
) -> dict:
    await _authorize_workspace(principal, "write")
    if await resource_collections.get_collection(app.state.pool, tenant_id=principal.tenant_id, collection_id=collection_id) is None:
        raise _collection_not_found(collection_id)
    await resource_collections.remove_member(
        app.state.pool, tenant_id=principal.tenant_id, collection_id=collection_id, resource_urn=resource_urn,
    )
    return {"status": "removed"}


@app.get("/api/resources/{urn}/collections")
async def list_resource_collections(urn: str, principal: Principal = Depends(current_principal)) -> list[dict]:
    await _authorize_workspace(principal, "read")
    await _authorize_resource(principal, _resource_authz_type(urn), urn, "read")
    return await resource_collections.list_collections_for_resource(app.state.pool, tenant_id=principal.tenant_id, resource_urn=urn)


@app.post("/api/applications/{name}/promote")
async def promote_application(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    await _get_application_or_404(name, principal, permission="write")
    authorization = _upstream_authorization(http_request) or ""
    try:
        result = await application_builder.promote(
            app.state.pool,
            app.state.client,
            tenant_id=principal.tenant_id,
            name=name,
            knowledge_url=KNOWLEDGE_URL,
            intelligence_url=INTELLIGENCE_URL,
            authorization=authorization,
        )
    except application_builder.InvalidApplicationDefinition as exc:
        raise HolonError.invalid_argument('ExperienceValidationFailed', str(exc)) from exc
    emit_audit(
        category="access",
        action="experience.application.promoted",
        outcome="success",
        tenant_id=principal.tenant_id,
        actor_urn=principal.urn,
        actor_type=principal.type,
        resource_type="application",
        resource_urn=application_builder.application_urn(principal.tenant_id, WORKSPACE_ID, name),
        extra={"name": name},
    )
    return result


async def _get_application_or_404(name: str, principal: Principal, *, permission: str = "read") -> dict:
    """Load an application only after enforcing its own ReBAC permission.

    Knowledge separately authorizes the data and actions behind an
    application.  This guard protects the application resource itself:
    its routes, form schema, dashboard layout, and agent configuration.
    """
    application = await application_builder.get_application(app.state.pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise _application_not_found(name)
    await _authorize_application(principal, application["urn"], permission)
    return application


def _upstream_detail(body: Any) -> str:
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


@app.get("/api/applications/{name}/data")
async def application_list_data(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Any:
    """Read list data for application objectApp surface."""
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no objectApp surface")
    authorization = _upstream_authorization(http_request)
    object_set = application_builder.resolve_object_app_object_set(application)
    if object_set:
        status_code, body = await _get_json(
            f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objectSets/{object_set}/objects", authorization=authorization
        )
        if status_code != 200:
            raise HolonError.from_http(status_code, _upstream_detail(body), error_name="UpstreamError")
        if not isinstance(body, dict):
            raise HolonError.from_http(502, "unexpected object-set evaluate response", error_name='UpstreamBadResponse')
        if body.get("object_type") != object_type:
            raise HolonError.invalid_argument('ObjectSetEvaluateFailed', (
                    f"object set {object_set!r} targets {body.get('object_type')!r}, "
                    f"not application ObjectType {object_type!r}"),
            )
        return body.get("data", body.get("items", []))
    return await _proxy(
        "GET", f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{object_type}", authorization=authorization
    )


@app.get("/api/applications/{name}/data/{instance_id}")
async def application_detail_data(
    name: str, instance_id: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no objectApp surface")
    return await _proxy(
        "GET",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{object_type}/{instance_id}",
        authorization=_upstream_authorization(http_request),
    )


@app.post("/api/applications/{name}/data/{instance_id}/actions/{action_name}")
async def application_invoke_action(
    name: str,
    instance_id: str,
    action_name: str,
    http_request: Request,
    principal: Principal = Depends(current_principal),
) -> Response:
    """Invoke action declared in application actionRefs."""
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no objectApp surface")
    if not application_builder.is_action_declared(application, object_type, action_name):
        raise HolonError.forbidden(
            "ActionNotInApplication",
            f"application {name!r} did not declare {object_type}.{action_name}",
            application=name,
            object_type=object_type,
            action_name=action_name,
        )

    authorization = _upstream_authorization(http_request)
    full_name = f"{object_type}.{action_name}"
    body = await http_request.json()
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{object_type}/{instance_id}/actions/{full_name}",
        authorization=authorization,
        json=body,
    )


@app.get("/api/applications/{name}/dashboard")
async def application_dashboard(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    """Fetch read-only widget data for application dashboard surface."""
    application = await _get_application_or_404(name, principal)
    authorization = _upstream_authorization(http_request)
    widgets_out = []
    for widget in application_builder.get_dashboard_widgets(application):
        object_set = widget.get("objectSet")
        if object_set:
            status_code, body = await _get_json(
                f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objectSets/{object_set}/objects", authorization=authorization
            )
            if status_code != 200:
                raise HolonError.from_http(status_code, _upstream_detail(body), error_name="UpstreamError")
            if not isinstance(body, dict):
                raise HolonError.from_http(502, "unexpected object-set evaluate response", error_name='UpstreamBadResponse')
            declared_type = widget.get("objectType")
            if declared_type and body.get("object_type") != declared_type:
                raise HolonError.invalid_argument('ObjectSetEvaluateFailed', (
                        f"object set {object_set!r} targets {body.get('object_type')!r}, "
                        f"not widget ObjectType {declared_type!r}"),
                )
            rows = body.get("data", []) if isinstance(body.get("data"), list) else (
                body.get("items", []) if isinstance(body.get("items"), list) else []
            )
        else:
            status_code, body = await _get_json(
                f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{widget['objectType']}", authorization=authorization
            )
            if status_code != 200:
                # `body` is whatever `_get_json` got back from upstream — usually
                # already a flat `{"detail": "..."}`, but proxying it verbatim as
                # `detail=body` would nest it (`{"detail": {"detail": "..."}}`)
                # instead of matching every other error response's flat shape.
                raise HolonError.from_http(status_code, _upstream_detail(body), error_name="UpstreamError")
            if isinstance(body, dict):
                rows = body.get("data") if isinstance(body.get("data"), list) else (
                    body.get("items") if isinstance(body.get("items"), list) else []
                )
            else:
                rows = body if isinstance(body, list) else []
        if widget["component"] == "kpi":
            widgets_out.append(
                {
                    "label": widget.get("label"),
                    "component": "kpi",
                    "value": len(rows),
                    "objectSet": object_set,
                }
            )
        elif widget["component"] == "table":
            widgets_out.append(
                {
                    "label": widget.get("label"),
                    "component": "table",
                    "rows": rows,
                    "objectSet": object_set,
                }
            )
        else:
            plugin_registration = await ui_component_registry.get_component_registration_by_name(
                app.state.pool, widget["component"]
            )
            widgets_out.append(
                {
                    "label": widget.get("label"),
                    "component": widget["component"],
                    "rows": rows,
                    "objectSet": object_set,
                    "iframeUrl": plugin_registration["manifest"]["iframe_url"] if plugin_registration else None,
                }
            )
    return {"applicationName": name, "widgets": widgets_out}


@app.post("/api/applications/{name}/analytics/execute")
async def application_analytics_execute(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Execute ad-hoc analytics query for application analytics surface."""
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_analytics_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no analytics surface")

    body = await http_request.json()
    if body.get("object_type") != object_type:
        raise HolonError.forbidden(
            "AnalyticsObjectTypeMismatch",
            f"application {name!r}'s analytics surface is scoped to {object_type!r}, not {body.get('object_type')!r}",
            application=name,
            expected_object_type=object_type,
            got_object_type=body.get("object_type"),
        )
    return await _proxy(
        "POST", f"{KNOWLEDGE_URL}/api/holon/execute", authorization=_upstream_authorization(http_request), json=body
    )


@app.post("/api/applications/{name}/analytics/{plan_hash}/replay")
async def application_analytics_replay(
    name: str, plan_hash: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Replay execution plan for application analytics surface."""
    application = await _get_application_or_404(name, principal)
    if application_builder.resolve_analytics_object_type(application) is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no analytics surface")
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/api/holon/execute/{plan_hash}/replay",
        authorization=_upstream_authorization(http_request),
    )


@app.get("/api/applications/{name}/form")
async def get_application_form(name: str, principal: Principal = Depends(current_principal)) -> dict:
    """The **form** surface — returns the declared field schema.
    """
    application = await _get_application_or_404(name, principal)
    form = application_builder.get_form_surface(application)
    if form is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no form surface")
    return {"action": form["action"], "fields": form["fields"]}


@app.post("/api/applications/{name}/form/{instance_id}")
async def submit_application_form(
    name: str, instance_id: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Validates the submission against the form's declared schema (required/type).
    """
    application = await _get_application_or_404(name, principal)
    form = application_builder.get_form_surface(application)
    if form is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no form surface")

    submitted = await http_request.json()
    try:
        application_builder.validate_form_submission(form, submitted)
    except application_builder.FormValidationError as exc:
        raise HolonError.invalid_argument('ExperienceValidationFailed', str(exc)) from exc

    object_type, local_action_name = form["action"].split(".", 1)
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/{object_type}/{instance_id}/actions/{local_action_name}",
        authorization=_upstream_authorization(http_request),
        json=submitted,
    )


@app.post("/api/applications/{name}/agent-sessions")
async def create_application_agent_session(name: str, principal: Principal = Depends(current_principal)) -> Response:
    """Create agent session for application agentApp surface."""
    application = await _get_application_or_404(name, principal)
    agent_app = application_builder.resolve_agent_app_config(application)
    if agent_app is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no agentApp surface")

    token = _agent_app_session_token(principal.urn)
    status_code, body = await _post_json(
        f"{INTELLIGENCE_URL}/sessions",
        authorization=f"Bearer {token}",
        json={
            "allowed_tools": agent_app.get("tools"),
            "system_prompt": agent_app.get("systemPrompt"),
            "budget": agent_app.get("budget"),
        },
    )
    if status_code == 200:
        await application_builder.record_agent_app_session(
            app.state.pool,
            session_urn=body["urn"],
            tenant_id=principal.tenant_id,
            application_name=name,
            created_by_urn=principal.urn,
        )
    return Response(content=json.dumps(body).encode(), status_code=status_code, media_type="application/json")


@app.post("/api/applications/{name}/agent-sessions/{session_urn:path}/turns")
async def run_application_agent_session_turn(
    name: str, session_urn: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Execute turn for application agentApp session."""
    await _get_application_or_404(name, principal)
    owner_urn = await application_builder.get_agent_app_session_owner(app.state.pool, session_urn)
    if owner_urn is None or owner_urn != principal.urn:
        raise HolonError.not_found('AgentSessionNotFound', f"no agent session {session_urn!r} found for this application")

    token = _agent_app_session_token(principal.urn)
    body = await http_request.json()
    return await _proxy(
        "POST", f"{INTELLIGENCE_URL}/sessions/{session_urn}/turns", authorization=f"Bearer {token}", json=body
    )


class RegisterUiComponentPluginRequest(BaseModel):
    entry_point: str


@app.post("/ui-component-plugins")
async def register_ui_component_plugin(
    body: RegisterUiComponentPluginRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Registers a UI component plugin. See `ui_component_registry.py`'s
    module docstring for details.

    A plugin controls the iframe URL rendered in every dashboard that uses
    its component.  It is therefore workspace-curation, not an operation
    available to every authenticated user.
    """
    await _authorize_workspace(principal, "write")
    try:
        return await ui_component_registry.register_ui_component_plugin(app.state.pool, entry_point=body.entry_point)
    except ui_component_registry.PluginConflictError as exc:
        raise HolonError.conflict('PluginConflict', str(exc)) from exc


def _ui_component_plugin_not_found(name: str) -> HolonError:
    return HolonError.not_found(
        "UiComponentPluginNotFound",
        f"no UI component plugin registered as {name!r}",
        name=name,
    )


@app.get("/ui-component-plugins/{name}")
async def get_ui_component_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "read")
    registration = await ui_component_registry.get_ui_component_registration(app.state.pool, name)
    if registration is None:
        raise _ui_component_plugin_not_found(name)
    return registration


@app.post("/ui-component-plugins/{name}/disable")
async def disable_ui_component_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    registration = await ui_component_registry.get_ui_component_registration(app.state.pool, name)
    if registration is None:
        raise _ui_component_plugin_not_found(name)
    return await ui_component_registry.set_ui_component_status(app.state.pool, name, "disabled")


@app.post("/ui-component-plugins/{name}/enable")
async def enable_ui_component_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    await _authorize_workspace(principal, "write")
    registration = await ui_component_registry.get_ui_component_registration(app.state.pool, name)
    if registration is None:
        raise _ui_component_plugin_not_found(name)
    return await ui_component_registry.set_ui_component_status(app.state.pool, name, "active")


# Hashed Vite chunks under /assets/ can be cached forever. index.html and
# unhashed files must revalidate — a cached shell after `npm run build`
# points at deleted filenames.
_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable"
_HTML_CACHE_CONTROL = "no-cache, must-revalidate"
_STATIC_ASSET_SUFFIXES = {
    ".css",
    ".eot",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".js",
    ".json",
    ".map",
    ".png",
    ".svg",
    ".ttf",
    ".webp",
    ".woff",
    ".woff2",
}


def _looks_like_static_asset(full_path: str) -> bool:
    if full_path.startswith("assets/"):
        return True
    return Path(full_path).suffix.lower() in _STATIC_ASSET_SUFFIXES


@app.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    """SPA shell + static asset server.

    Registered last so every real API route above wins the match first.
    Serves the requested file if it exists under STATIC_DIR (built JS/CSS
    chunks, favicon, ...); otherwise falls back to index.html so the
    client-side router can render deep links (e.g. a hard refresh on
    `/applications/foo`). Missing hashed assets return 404 — never HTML —
    so a stale import does not execute the shell as a module.
    """
    index = STATIC_DIR / "index.html"
    candidate = (STATIC_DIR / full_path).resolve()
    if full_path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
        cache = _ASSET_CACHE_CONTROL if full_path.startswith("assets/") else _HTML_CACHE_CONTROL
        return FileResponse(candidate, headers={"Cache-Control": cache})
    if _looks_like_static_asset(full_path):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(index, headers={"Cache-Control": _HTML_CACHE_CONTROL})
