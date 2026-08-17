"""Experience Platform — serves the Holon React SPA and its Application
Builder API. It calls Identity and Knowledge over HTTP like any other client.
Application Builder JSON endpoints live under `/api/*` so they never collide with the
SPA's client-side routes (`/applications`, `/objects`, etc.) — the catch-all
serves `index.html` for any other path and lets the SPA's router take over.
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, Request, Response
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
)
from holon_common.audit import clear_durable_audit_hooks, emit_audit
from holon_common.audit_store import (
    ensure_schema as ensure_audit_schema,
    install_durable_audit,
    list_events_page,
)
from holon_common.authz import PermissionClient

from . import application_builder, project_pins, resource_tags, ui_component_registry
from . import collections as resource_collections

SERVICE_NAME = "experience-platform"
configure_json_logging(SERVICE_NAME)

IDENTITY_URL = os.environ["HOLON_IDENTITY_URL"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
INTELLIGENCE_URL = os.environ["HOLON_INTELLIGENCE_URL"]
TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET, JWT_ACTIVE_KID, JWT_SECRETS = active_jwt()
DB_URL = os.environ["HOLON_DB_URL"]
OTLP_ENDPOINT = os.environ.get("HOLON_OTLP_ENDPOINT", "")

SPICEDB_URL = os.environ["HOLON_SPICEDB_URL"]
SPICEDB_PRESHARED_KEY = os.environ["HOLON_SPICEDB_PRESHARED_KEY"]
OPA_URL = os.environ["HOLON_OPA_URL"]
SPICEDB_SCHEMA_PATH = os.environ["HOLON_SPICEDB_SCHEMA_PATH"]

WORKSPACE_URN = build_urn(TENANT_ID, "global", "workspace", WORKSPACE_ID)

AGENT_URN = build_urn(TENANT_ID, "global", "agent", "ingest-bot")


def _allow_dev_login() -> bool:
    return os.environ.get("HOLON_ALLOW_DEV_LOGIN", "true").lower() in {"1", "true", "yes"}


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
    async with app.state.pool.acquire() as conn:
        await application_builder.ensure_schema(conn)
        await ui_component_registry.ensure_schema(conn)
        await resource_tags.ensure_schema(conn)
        await project_pins.ensure_schema(conn)
        await resource_collections.ensure_schema(conn)
        await ensure_audit_schema(conn)
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

    yield
    await app.state.pool.close()
    await app.state.client.aclose()


app = FastAPI(title="Holon — Experience Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
install_error_handlers(app, service_name=SERVICE_NAME)
current_principal = make_principal_dependency(JWT_SECRET, secrets=JWT_SECRETS)


class TokenRequest(BaseModel):
    principal_urn: str


class CreditHoldRequest(BaseModel):
    reason: str


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


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/live")
async def live() -> dict:
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict:
    await app.state.pool.fetchval("SELECT 1")
    return {"status": "ok"}


@app.get("/api/config")
async def config() -> dict:
    return {
        "tenant_id": TENANT_ID,
        "workspace_id": WORKSPACE_ID,
        "default_user_urn": f"hl:{TENANT_ID}:global:user:jdoe",
        "customer_object_type_urn": f"hl:{TENANT_ID}:{WORKSPACE_ID}:object-type:Customer",
        "allow_dev_login": _allow_dev_login(),
        "intelligence_enabled": _intelligence_enabled(),
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


@app.post("/api/token")
async def mint_token(request: TokenRequest, principal: Principal = Depends(current_principal)) -> Response:
    """Dev-only token mint helper — derives `*-dev-secret` server-side.
    Disabled when `HOLON_ALLOW_DEV_LOGIN` is false. Prefer Identity
    `/login` (cookie session) or OIDC.
    """
    if not _allow_dev_login():
        raise HolonError.forbidden('PrincipalDisabled', "demo token proxy disabled (set HOLON_ALLOW_DEV_LOGIN=true for local demo)",)
    if request.principal_urn != principal.urn:
        raise HolonError.forbidden('ForbiddenMint', "cannot mint a token for another principal")
    local_name = request.principal_urn.rsplit(":", 1)[-1]
    client_secret = f"{local_name}-dev-secret"
    return await _proxy(
        "POST", f"{IDENTITY_URL}/token", json={"principal_urn": request.principal_urn, "client_secret": client_secret}
    )


@app.get("/api/customers")
async def list_customers(request: Request) -> Response:
    return await _proxy(
        "GET",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/Customer",
        authorization=request.headers.get("authorization"),
    )


@app.get("/api/lineage/{urn:path}")
async def get_lineage(urn: str, request: Request) -> Response:
    return await _proxy(
        "GET",
        f"{KNOWLEDGE_URL}/api/holon/lineage/{urn}",
        authorization=request.headers.get("authorization"),
    )


@app.get("/api/customers/{customer_id}/orders")
async def get_customer_orders(customer_id: int, request: Request) -> Response:
    return await _proxy(
        "GET",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/Customer/{customer_id}/orders",
        authorization=request.headers.get("authorization"),
    )


@app.post("/api/customers/{customer_id}/credit-hold")
async def put_customer_on_credit_hold(customer_id: int, body: CreditHoldRequest, request: Request) -> Response:
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/api/ontologies/{WORKSPACE_ID}/objects/Customer/{customer_id}/actions/putOnCreditHold",
        authorization=request.headers.get("authorization"),
        json=body.model_dump(),
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
        # Creating a brand-new Application: there's no `parent_workspace`
        # relation yet for `_authorize_application` to check *against* (it
        # doesn't exist until right after this), so the gate has to be the
        # workspace's own `write` instead — same "check the container
        # before the thing inside it exists" shape `knowledge`'s self-serve
        # ObjectType creation already uses (`_authorize_ontology_governance`).
        # Without this, any authenticated principal — including one with
        # zero workspace grants — could mint a new Application, since the
        # relation-write below would happily grant *that* URN to the
        # workspace regardless of who asked.
        decision = await app.state.authz.authorize(
            principal, resource_type="workspace", resource_urn=WORKSPACE_URN, permission="write",
        )
        if not decision.allowed:
            raise HolonError.forbidden("PermissionDenied", decision.reason)

    authorization = http_request.headers.get("authorization", "")
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
    # pinned, only curating (pin/unpin above) needs write.
    await _authorize_project(principal, project_urn, "read")
    return await project_pins.list_pins(app.state.pool, tenant_id=principal.tenant_id, project_urn=project_urn)


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
    authorization = http_request.headers.get("authorization", "")
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
    """The 'object app' surface's list view: reads through Knowledge's
    permission-gated `/objects/{type}` (or `/object-sets/{name}/objects`
    when the surface declares an Object Set filter) using the caller's token.
    """
    application = await _get_application_or_404(name, principal)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no objectApp surface")
    authorization = http_request.headers.get("authorization")
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
        authorization=http_request.headers.get("authorization"),
    )


@app.post("/api/applications/{name}/data/{instance_id}/actions/{action_name}")
async def application_invoke_action(
    name: str,
    instance_id: str,
    action_name: str,
    http_request: Request,
    principal: Principal = Depends(current_principal),
) -> Response:
    """An application can only invoke an Action it explicitly declared in `actionRefs`.

    Every Action Type is declarative, so the generic
    `/objects/{object_type}/{id}/actions/{full_name}` route (keyed by the
    full dotted name) is the only shape to proxy to.
    """
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

    authorization = http_request.headers.get("authorization")
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
    """The **dashboard** surface — a page of read-only widgets,
    each bound to an ObjectType and optionally narrowed by an Object Set.
    """
    application = await _get_application_or_404(name, principal)
    authorization = http_request.headers.get("authorization")
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
    """The **analytics** surface: a lightweight Contour/Code
    Workbook equivalent — ad-hoc pivot/aggregate/join exploration, unlike
    every other surface's fixed read path. Bounded to the one ObjectType
    the surface declared: the request body's `object_type` must match it
    exactly, the same enforcement style `is_action_declared` already uses
    for forms/objectApp actions. Everything else (which operation, which
    property to group by, which RelationType to join) is genuinely ad-hoc,
    proxied straight through to Knowledge's real `/execute` — this
    endpoint's only job is scoping, not reimplementing Knowledge's own
    validation.
    """
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
        "POST", f"{KNOWLEDGE_URL}/api/holon/execute", authorization=http_request.headers.get("authorization"), json=body
    )


@app.post("/api/applications/{name}/analytics/{plan_hash}/replay")
async def application_analytics_replay(
    name: str, plan_hash: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Replay is a pure pass-through — Knowledge's own `/execute/{plan_hash}/replay`
    already re-authorizes against whatever ObjectType(s) the frozen plan
    actually touches (including a `join` plan's target), so there's
    nothing left for this scoping check to add beyond confirming the
    application really does have an analytics surface at all.
    """
    application = await _get_application_or_404(name, principal)
    if application_builder.resolve_analytics_object_type(application) is None:
        raise HolonError.invalid_argument('ApplicationSurfaceMissing', f"application {name!r} declares no analytics surface")
    return await _proxy(
        "POST", f"{KNOWLEDGE_URL}/api/holon/execute/{plan_hash}/replay", authorization=http_request.headers.get("authorization")
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
        authorization=http_request.headers.get("authorization"),
        json=submitted,
    )


@app.post("/api/applications/{name}/agent-sessions")
async def create_application_agent_session(name: str, principal: Principal = Depends(current_principal)) -> Response:
    """The **agent app** surface: compiles the surface's
    declared `tools`/`systemPrompt`/`budget` into a real Intelligence
    session, opened under the shared `ingest-bot` agent identity
    `on_behalf_of` the calling principal — the same delegation model
    `agent_runtime`'s own module docstring already establishes (effective
    rights are the *intersection* of the agent's and the mandant's, never
    an escalation). `record_agent_app_session` is what lets the turn
    endpoint below tell this caller's session apart from anyone else's,
    since every agentApp session shares one underlying agent identity.
    """
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
    """Every turn is proxied, never handed off — the calling principal
    can't call Intelligence directly (it doesn't hold the shared agent
    identity's credentials), and this is also the choke point that
    enforces only the principal who *launched* this specific session may
    drive it, checked against `agent_app_session` rather than trusting
    the shared agent identity alone to tell callers apart.
    """
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


@app.get("/{full_path:path}")
async def spa(full_path: str) -> FileResponse:
    """SPA shell + static asset server.

    Registered last so every real API route above wins the match first.
    Serves the requested file if it exists under STATIC_DIR (built JS/CSS
    chunks, favicon, ...); otherwise falls back to index.html so the
    client-side router can render deep links (e.g. a hard refresh on
    `/applications/foo`).
    """
    index = STATIC_DIR / "index.html"
    candidate = (STATIC_DIR / full_path).resolve()
    if full_path and candidate.is_file() and STATIC_DIR.resolve() in candidate.parents:
        return FileResponse(candidate)
    return FileResponse(index)
