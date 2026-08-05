"""Experience Platform — serves the Holon React SPA and its Application
Builder API. It calls Identity and Knowledge over HTTP like any other client.
Application Builder JSON endpoints live under `/api/*` so they never collide with the
SPA's client-side routes (`/applications`, `/objects`, etc.) — the catch-all
serves `index.html` for any other path and lets the SPA's router take over.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Optional

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel

from holon_common import (
    CircuitBreaker,
    CircuitBreakerOpenError,
    Principal,
    configure_json_logging,
    create_pool,
    instrument_cors,
    instrument_metrics,
    instrument_tracing,
    make_principal_dependency,
)

from . import application_builder, ui_component_registry

SERVICE_NAME = "experience-platform"
configure_json_logging(SERVICE_NAME)

IDENTITY_URL = os.environ["HOLON_IDENTITY_URL"]
KNOWLEDGE_URL = os.environ["HOLON_KNOWLEDGE_URL"]
TENANT_ID = os.environ["HOLON_TENANT_ID"]
WORKSPACE_ID = os.environ["HOLON_WORKSPACE_ID"]
JWT_SECRET = os.environ["HOLON_JWT_SECRET"]
DB_URL = os.environ["HOLON_DB_URL"]
OTLP_ENDPOINT = os.environ["HOLON_OTLP_ENDPOINT"]

STATIC_DIR = Path(__file__).parent / "static"

_TIMEOUT_SECONDS = 5.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Bulkhead: one long-lived client with a capped connection
    # pool, not a throwaway `httpx.AsyncClient()` per proxied request.
    app.state.client = httpx.AsyncClient(
        timeout=_TIMEOUT_SECONDS, limits=httpx.Limits(max_connections=20, max_keepalive_connections=10)
    )
    app.state.breaker = CircuitBreaker(name="experience-proxy", failure_threshold=5, cooldown_seconds=30.0)

    # Experience database (application_builder.py).
    # Application definitions are owned by Experience.
    app.state.pool = await create_pool(DB_URL)
    async with app.state.pool.acquire() as conn:
        await application_builder.ensure_schema(conn)
        await ui_component_registry.ensure_schema(conn)

    yield
    await app.state.pool.close()
    await app.state.client.aclose()


app = FastAPI(title="Holon — Experience Platform", lifespan=lifespan)
instrument_cors(app)
instrument_metrics(app, service_name=SERVICE_NAME)
instrument_tracing(app, service_name=SERVICE_NAME, otlp_endpoint=OTLP_ENDPOINT)
current_principal = make_principal_dependency(JWT_SECRET)


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
    return upstream.status_code, upstream.json()


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
    }


@app.post("/api/token")
async def mint_token(request: TokenRequest) -> Response:
    """Identity's `/token` has required a `client_secret` since increment
    #13 (closing the "mint a token for any URN, no check at all" gap) —
    this proxy never forwarded one, so the dashboard's own sign-in has been
    broken since then. Derived here, server-side, using the same
    deterministic dev-only convention every test file already computes
    independently (`seed.client_secret_for`: `f"{local_name}-dev-secret"`)
    — the frontend stays as simple as it was, it only ever knew a URN.
    """
    local_name = request.principal_urn.rsplit(":", 1)[-1]
    client_secret = f"{local_name}-dev-secret"
    return await _proxy(
        "POST", f"{IDENTITY_URL}/token", json={"principal_urn": request.principal_urn, "client_secret": client_secret}
    )


@app.get("/api/customers")
async def list_customers(request: Request) -> Response:
    return await _proxy("GET", f"{KNOWLEDGE_URL}/objects/Customer", authorization=request.headers.get("authorization"))


@app.get("/api/lineage/{urn:path}")
async def get_lineage(urn: str, request: Request) -> Response:
    return await _proxy("GET", f"{KNOWLEDGE_URL}/lineage/{urn}", authorization=request.headers.get("authorization"))


@app.get("/api/customers/{customer_id}/orders")
async def get_customer_orders(customer_id: int, request: Request) -> Response:
    return await _proxy(
        "GET",
        f"{KNOWLEDGE_URL}/objects/Customer/{customer_id}/orders",
        authorization=request.headers.get("authorization"),
    )


@app.post("/api/customers/{customer_id}/credit-hold")
async def put_customer_on_credit_hold(customer_id: int, body: CreditHoldRequest, request: Request) -> Response:
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/objects/Customer/{customer_id}/actions/putOnCreditHold",
        authorization=request.headers.get("authorization"),
        json=body.model_dump(),
    )


class ApplicationDefinitionRequest(BaseModel):
    definition: dict[str, Any]


def _application_not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no application named {name!r}")


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
    authorization = http_request.headers.get("authorization", "")
    try:
        return await application_builder.create_or_update_draft(
            app.state.pool,
            app.state.client,
            tenant_id=principal.tenant_id,
            name=name,
            definition=body.definition,
            knowledge_url=KNOWLEDGE_URL,
            authorization=authorization,
        )
    except application_builder.InvalidApplicationDefinition as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/applications")
async def list_applications(principal: Principal = Depends(current_principal)) -> list[dict]:
    """A real, previously-missing gap — every prior verification already
    knew the Application's name from creating it. Returns the latest
    version of each distinct application for this tenant.
    """
    return await application_builder.list_applications(app.state.pool, tenant_id=principal.tenant_id)


@app.get("/api/applications/{name}")
async def get_application(name: str, principal: Principal = Depends(current_principal)) -> dict:
    application = await application_builder.get_application(app.state.pool, tenant_id=principal.tenant_id, name=name)
    if application is None:
        raise _application_not_found(name)
    return application


@app.post("/api/applications/{name}/promote")
async def promote_application(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    authorization = http_request.headers.get("authorization", "")
    try:
        return await application_builder.promote(
            app.state.pool,
            app.state.client,
            tenant_id=principal.tenant_id,
            name=name,
            knowledge_url=KNOWLEDGE_URL,
            authorization=authorization,
        )
    except application_builder.InvalidApplicationDefinition as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _get_application_or_404(name: str, tenant_id: str) -> dict:
    application = await application_builder.get_application(app.state.pool, tenant_id=tenant_id, name=name)
    if application is None:
        raise _application_not_found(name)
    return application


@app.get("/api/applications/{name}/data")
async def application_list_data(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """The 'object app' surface's list view: reads through Knowledge's
    permission-gated `/objects/{type}` endpoint using the caller's token.
    """
    application = await _get_application_or_404(name, principal.tenant_id)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HTTPException(status_code=400, detail=f"application {name!r} declares no objectApp surface")
    return await _proxy(
        "GET", f"{KNOWLEDGE_URL}/objects/{object_type}", authorization=http_request.headers.get("authorization")
    )


@app.get("/api/applications/{name}/data/{instance_id}")
async def application_detail_data(
    name: str, instance_id: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    application = await _get_application_or_404(name, principal.tenant_id)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HTTPException(status_code=400, detail=f"application {name!r} declares no objectApp surface")
    return await _proxy(
        "GET",
        f"{KNOWLEDGE_URL}/objects/{object_type}/{instance_id}",
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
    """
    application = await _get_application_or_404(name, principal.tenant_id)
    object_type = application_builder.resolve_object_app_object_type(application)
    if object_type is None:
        raise HTTPException(status_code=400, detail=f"application {name!r} declares no objectApp surface")
    if not application_builder.is_action_declared(application, object_type, action_name):
        raise HTTPException(
            status_code=403, detail=f"application {name!r} did not declare {object_type}.{action_name}"
        )
    body = await http_request.json()
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/objects/{object_type}/{instance_id}/actions/{action_name}",
        authorization=http_request.headers.get("authorization"),
        json=body,
    )


@app.get("/api/applications/{name}/dashboard")
async def application_dashboard(
    name: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> dict:
    """The **dashboard** surface — a page of read-only widgets,
    each bound to an ObjectType.
    """
    application = await _get_application_or_404(name, principal.tenant_id)
    authorization = http_request.headers.get("authorization")
    widgets_out = []
    for widget in application_builder.get_dashboard_widgets(application):
        status_code, body = await _get_json(
            f"{KNOWLEDGE_URL}/objects/{widget['objectType']}", authorization=authorization
        )
        if status_code != 200:
            raise HTTPException(status_code=status_code, detail=body)
        rows = body if isinstance(body, list) else []
        if widget["component"] == "kpi":
            widgets_out.append({"label": widget.get("label"), "component": "kpi", "value": len(rows)})
        elif widget["component"] == "table":
            widgets_out.append({"label": widget.get("label"), "component": "table", "rows": rows})
        else:
            plugin_registration = await ui_component_registry.get_component_registration_by_name(
                app.state.pool, widget["component"]
            )
            widgets_out.append(
                {
                    "label": widget.get("label"),
                    "component": widget["component"],
                    "rows": rows,
                    "iframeUrl": plugin_registration["manifest"]["iframe_url"] if plugin_registration else None,
                }
            )
    return {"applicationName": name, "widgets": widgets_out}


@app.get("/api/applications/{name}/form")
async def get_application_form(name: str, principal: Principal = Depends(current_principal)) -> dict:
    """The **form** surface — returns the declared field schema.
    """
    application = await _get_application_or_404(name, principal.tenant_id)
    form = application_builder.get_form_surface(application)
    if form is None:
        raise HTTPException(status_code=400, detail=f"application {name!r} declares no form surface")
    return {"action": form["action"], "fields": form["fields"]}


@app.post("/api/applications/{name}/form/{instance_id}")
async def submit_application_form(
    name: str, instance_id: str, http_request: Request, principal: Principal = Depends(current_principal)
) -> Response:
    """Validates the submission against the form's declared schema (required/type).
    """
    application = await _get_application_or_404(name, principal.tenant_id)
    form = application_builder.get_form_surface(application)
    if form is None:
        raise HTTPException(status_code=400, detail=f"application {name!r} declares no form surface")

    submitted = await http_request.json()
    try:
        application_builder.validate_form_submission(form, submitted)
    except application_builder.FormValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    object_type, local_action_name = form["action"].split(".", 1)
    return await _proxy(
        "POST",
        f"{KNOWLEDGE_URL}/objects/{object_type}/{instance_id}/actions/{local_action_name}",
        authorization=http_request.headers.get("authorization"),
        json=submitted,
    )


class RegisterUiComponentPluginRequest(BaseModel):
    entry_point: str


@app.post("/ui-component-plugins")
async def register_ui_component_plugin(
    body: RegisterUiComponentPluginRequest, principal: Principal = Depends(current_principal)
) -> dict:
    """Registers a UI component plugin. See `ui_component_registry.py`'s
    module docstring for details.
    """
    try:
        return await ui_component_registry.register_ui_component_plugin(app.state.pool, entry_point=body.entry_point)
    except ui_component_registry.PluginConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _ui_component_plugin_not_found(name: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"no UI component plugin registered as {name!r}")


@app.get("/ui-component-plugins/{name}")
async def get_ui_component_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await ui_component_registry.get_ui_component_registration(app.state.pool, name)
    if registration is None:
        raise _ui_component_plugin_not_found(name)
    return registration


@app.post("/ui-component-plugins/{name}/disable")
async def disable_ui_component_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
    registration = await ui_component_registry.get_ui_component_registration(app.state.pool, name)
    if registration is None:
        raise _ui_component_plugin_not_found(name)
    return await ui_component_registry.set_ui_component_status(app.state.pool, name, "disabled")


@app.post("/ui-component-plugins/{name}/enable")
async def enable_ui_component_plugin(name: str, principal: Principal = Depends(current_principal)) -> dict:
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
