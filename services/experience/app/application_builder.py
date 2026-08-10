"""Experience Platform — Application Builder.

An Application Builder linked to the ontology:
all five surfaces are built (**object app**, **dashboard**, **form**,
**agent app**, **analytics**).

- Applications only declare ObjectTypes/Actions that genuinely exist in Knowledge's ontology.
- Dependencies are computed automatically from bindings/actionRefs.
- Applications are versioned resources (draft -> promoted).
- Forms declare validation field schemas matching declared actions.
- An `agentApp` surface declares a tool allowlist, a system-prompt
  template, and budget defaults — a non-engineer's way to configure a
  bounded agent, compiled into a real Intelligence session by `main.py`'s
  "run" endpoints.
- An `analytics` surface scopes ad-hoc pivot/aggregate/join
  exploration to one declared ObjectType, proxying real `ExecutionRequest`s
  through to Knowledge's own `/execute` (`resolve_analytics_object_type`).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import httpx

from holon_common import build_urn

from . import ui_component_registry

logger = logging.getLogger("experience.application_builder")

DDL = """
CREATE TABLE IF NOT EXISTS application (
    id BIGSERIAL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    name TEXT NOT NULL,
    version INT NOT NULL,
    definition JSONB NOT NULL,
    dependencies JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_at TIMESTAMPTZ,
    UNIQUE (tenant_id, name, version)
);

-- ReBAC hardening: previously Applications had no URN and no SpiceDB
-- relation at all (any authenticated principal could read/write any
-- application). One URN per (tenant, name) — not per version, since a
-- version is an internal draft/promote detail, not a separately
-- addressable resource the way an ObjectType's URN@version is.
ALTER TABLE application ADD COLUMN IF NOT EXISTS urn TEXT;

-- Project scoping — same single-valued-per-(tenant,name), not-per-
-- version shape as `urn` above (an Application's project membership is a
-- workspace-organization fact, not a governed/versioned one the way
-- ObjectType's project_urn is tied to its propose/publish lifecycle).
ALTER TABLE application ADD COLUMN IF NOT EXISTS project_urn TEXT;

-- an `agentApp` session is opened under the single shared
-- `ingest-bot` agent identity (Intelligence's `POST /sessions` requires
-- the caller *be* the agent — a human principal never holds that
-- identity's credentials), so Experience must proxy every subsequent
-- turn using a freshly-minted token for that same identity. Without this
-- table, *any* authenticated principal who learned a `session_urn` could
-- drive *any other* principal's agentApp conversation, since the shared
-- agent identity alone can't distinguish who originally launched it —
-- `created_by_urn` is what `main.py`'s turn endpoint checks instead.
CREATE TABLE IF NOT EXISTS agent_app_session (
    session_urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    application_name TEXT NOT NULL,
    created_by_urn TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


def application_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    return build_urn(tenant_id, workspace_id, "application", name)


async def backfill_urns(pool: asyncpg.Pool, *, tenant_id: str, workspace_id: str) -> list[str]:
    """One-time (but idempotent — safe every startup) catch-up for
    applications created before Applications had a `urn` column at all.
    Returns the names that were actually backfilled, so the caller
    (`main.py`'s lifespan) knows exactly which ones still need their
    `parent_workspace` SpiceDB relationship written too — a pre-existing
    application row is otherwise indistinguishable from one a brand-new
    create request should grant a relation for.
    """
    rows = await pool.fetch(
        "SELECT DISTINCT name FROM application WHERE tenant_id = $1 AND urn IS NULL", tenant_id,
    )
    names = [row["name"] for row in rows]
    for name in names:
        await pool.execute(
            "UPDATE application SET urn = $1 WHERE tenant_id = $2 AND name = $3",
            application_urn(tenant_id, workspace_id, name), tenant_id, name,
        )
    return names


async def record_agent_app_session(
    pool: asyncpg.Pool, *, session_urn: str, tenant_id: str, application_name: str, created_by_urn: str
) -> None:
    await pool.execute(
        """
        INSERT INTO agent_app_session (session_urn, tenant_id, application_name, created_by_urn)
        VALUES ($1, $2, $3, $4)
        """,
        session_urn, tenant_id, application_name, created_by_urn,
    )


async def get_agent_app_session_owner(pool: asyncpg.Pool, session_urn: str) -> Optional[str]:
    return await pool.fetchval("SELECT created_by_urn FROM agent_app_session WHERE session_urn = $1", session_urn)


class InvalidApplicationDefinition(ValueError):
    pass


class FormValidationError(ValueError):
    """A form submission at runtime, not a definition problem — kept
    distinct from `InvalidApplicationDefinition` since it's raised on
    every submit, not just at draft/promote time.
    """


_VALID_FIELD_TYPES = {"string", "integer", "boolean"}
_VALID_BUDGET_KEYS = {"max_iterations", "max_tool_calls", "max_tokens"}


def _referenced_object_types(definition: dict) -> set[str]:
    types = {s["objectType"] for s in definition.get("surfaces", []) if "objectType" in s}
    types |= {b["objectType"] for b in definition.get("bindings", [])}
    for surface in definition.get("surfaces", []):
        if surface.get("type") == "dashboard":
            types |= {w["objectType"] for w in surface.get("widgets", []) if "objectType" in w}
    return types


def _referenced_actions(definition: dict) -> set[str]:
    return {a["action"] for a in definition.get("actionRefs", [])}


def _form_surfaces(definition: dict) -> list[dict]:
    return [s for s in definition.get("surfaces", []) if s.get("type") == "form"]


def _agent_app_surfaces(definition: dict) -> list[dict]:
    return [s for s in definition.get("surfaces", []) if s.get("type") == "agentApp"]


def _referenced_tools(definition: dict) -> set[str]:
    tools: set[str] = set()
    for surface in _agent_app_surfaces(definition):
        tools |= set(surface.get("tools", []))
    return tools


def _referenced_components(definition: dict) -> set[str]:
    components = {b["component"] for b in definition.get("bindings", []) if "component" in b}
    for surface in definition.get("surfaces", []):
        if surface.get("type") == "dashboard":
            components |= {w["component"] for w in surface.get("widgets", []) if "component" in w}
    return components


async def _validate_definition(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    knowledge_url: str,
    intelligence_url: str,
    authorization: str,
    definition: dict,
) -> dict:
    """An application can only declare ObjectTypes/Actions that genuinely
    exist in Knowledge's ontology — the set of things it's even allowed to
    bind to is bounded by the ontology API. Component names are checked the
    identical way, against the UI component plugin registry or built-in components.
    An `agentApp` surface's declared `tools` are checked the same way
    again, against Intelligence's own live tool catalog (`GET /tools`) —
    only if any are actually declared, the same "don't pay for a
    cross-service call nothing referenced" discipline every other check
    here already follows.
    """
    headers = {"Authorization": authorization}
    object_types = sorted(_referenced_object_types(definition))
    actions = sorted(_referenced_actions(definition))
    components = sorted(_referenced_components(definition))
    tools = sorted(_referenced_tools(definition))

    for object_type in object_types:
        response = await http.get(f"{knowledge_url}/ontology/{object_type}", headers=headers)
        if response.status_code == 404:
            raise InvalidApplicationDefinition(f"unknown ObjectType {object_type!r}")
        response.raise_for_status()

    for action in actions:
        response = await http.get(f"{knowledge_url}/actions/{action}", headers=headers)
        if response.status_code == 404:
            raise InvalidApplicationDefinition(f"unknown Action {action!r}")
        response.raise_for_status()

    for component in components:
        if not await ui_component_registry.is_valid_component_name(pool, component):
            raise InvalidApplicationDefinition(f"unknown component {component!r} — not built-in or a registered plugin")

    for form in _form_surfaces(definition):
        if form.get("action") not in actions:
            raise InvalidApplicationDefinition(
                f"form surface references action {form.get('action')!r}, which isn't in this "
                f"application's own actionRefs"
            )
        for field in form.get("fields", []):
            if field.get("type") not in _VALID_FIELD_TYPES:
                raise InvalidApplicationDefinition(
                    f"form field {field.get('name')!r} has invalid type {field.get('type')!r}"
                )

    for agent_app in _agent_app_surfaces(definition):
        if not agent_app.get("systemPrompt"):
            raise InvalidApplicationDefinition("agentApp surface requires a non-empty systemPrompt")
        budget = agent_app.get("budget", {})
        if not isinstance(budget, dict) or not set(budget.keys()) <= _VALID_BUDGET_KEYS:
            raise InvalidApplicationDefinition(f"agentApp budget may only declare {sorted(_VALID_BUDGET_KEYS)}")
        for key, value in budget.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise InvalidApplicationDefinition(f"agentApp budget.{key} must be a positive integer")

    if tools:
        response = await http.get(f"{intelligence_url}/tools", headers=headers)
        response.raise_for_status()
        available_tool_names = {t["name"] for t in response.json()}
        unknown_tools = [t for t in tools if t not in available_tool_names]
        if unknown_tools:
            raise InvalidApplicationDefinition(f"unknown tool(s) declared: {unknown_tools}")

    return {"objectTypes": object_types, "actions": actions}


async def list_applications(pool: asyncpg.Pool, *, tenant_id: str) -> list[dict]:
    """A real, previously-missing gap: an Application couldn't be
    discovered at all without already knowing its name — every prior
    verification in this build called `get_application` by a name it
    already had from creating the app itself. Returns the latest version
    of every distinct application name for this tenant.
    """
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (name) *
        FROM application
        WHERE tenant_id = $1
        ORDER BY name, version DESC
        """,
        tenant_id,
    )
    results = []
    for row in rows:
        result = dict(row)
        for field in ("definition", "dependencies"):
            if isinstance(result[field], str):
                result[field] = json.loads(result[field])
        results.append(result)
    return results


async def set_application_project(
    pool: asyncpg.Pool, *, tenant_id: str, name: str, project_urn: Optional[str]
) -> Optional[dict]:
    """Direct set/clear — unlike ObjectType's `project_urn`, this isn't
    tied to a propose/publish governance workflow (Applications have no
    branching/versioned-governance lifecycle), so there's no draft to
    stage it on. Applies to every version row for this name, same
    "identity fact, not a per-version one" treatment `urn` already gets.
    """
    await pool.execute(
        "UPDATE application SET project_urn = $1 WHERE tenant_id = $2 AND name = $3", project_urn, tenant_id, name,
    )
    return await get_application(pool, tenant_id=tenant_id, name=name)


async def get_application(pool: asyncpg.Pool, *, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow(
        "SELECT * FROM application WHERE tenant_id = $1 AND name = $2 ORDER BY version DESC LIMIT 1",
        tenant_id, name,
    )
    if row is None:
        return None
    result = dict(row)
    for field in ("definition", "dependencies"):
        if isinstance(result[field], str):
            result[field] = json.loads(result[field])
    return result


async def create_or_update_draft(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    workspace_id: str,
    name: str,
    definition: dict,
    knowledge_url: str,
    intelligence_url: str,
    authorization: str,
) -> dict:
    dependencies = await _validate_definition(
        pool, http, knowledge_url=knowledge_url, intelligence_url=intelligence_url,
        authorization=authorization, definition=definition,
    )
    existing = await get_application(pool, tenant_id=tenant_id, name=name)

    if existing is None:
        await pool.execute(
            "INSERT INTO application (tenant_id, name, version, definition, dependencies, status, urn) "
            "VALUES ($1, $2, 1, $3::jsonb, $4::jsonb, 'draft', $5)",
            tenant_id, name, json.dumps(definition), json.dumps(dependencies),
            application_urn(tenant_id, workspace_id, name),
        )
    elif existing["status"] == "draft":
        # Editing an unpromoted draft in place — not yet "live"
        # (nothing has promoted it), so in-place edits are
        # exactly what a draft is for.
        await pool.execute(
            "UPDATE application SET definition = $1::jsonb, dependencies = $2::jsonb WHERE id = $3",
            json.dumps(definition), json.dumps(dependencies), existing["id"],
        )
    else:
        # The latest version is already promoted (immutable);
        # further changes always create a new draft, never edit it live.
        # `urn`/`project_urn` carry forward from the prior version — they're
        # identity/organization facts about the Application, not something
        # a new draft resets (a bug fixed here: this branch previously left
        # them NULL on the new row, which `get_application`'s `ORDER BY
        # version DESC LIMIT 1` would then surface as the "current" urn).
        await pool.execute(
            "INSERT INTO application (tenant_id, name, version, definition, dependencies, status, urn, project_urn) "
            "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, 'draft', $6, $7)",
            tenant_id, name, existing["version"] + 1, json.dumps(definition), json.dumps(dependencies),
            existing["urn"], existing.get("project_urn"),
        )

    return await get_application(pool, tenant_id=tenant_id, name=name)


async def promote(
    pool: asyncpg.Pool,
    http: httpx.AsyncClient,
    *,
    tenant_id: str,
    name: str,
    knowledge_url: str,
    intelligence_url: str,
    authorization: str,
) -> dict:
    application = await get_application(pool, tenant_id=tenant_id, name=name)
    if application is None:
        raise InvalidApplicationDefinition(f"no application named {name!r}")
    if application["status"] != "draft":
        raise InvalidApplicationDefinition(f"application {name!r} version {application['version']} is already promoted")

    # Re-validate at promotion time, not just at draft creation — the
    # ontology may have changed (an ObjectType/Action deprecated) since
    # the draft was written.
    await _validate_definition(
        pool, http, knowledge_url=knowledge_url, intelligence_url=intelligence_url,
        authorization=authorization, definition=application["definition"],
    )

    await pool.execute(
        "UPDATE application SET status = 'promoted', promoted_at = $1 WHERE id = $2",
        datetime.now(timezone.utc), application["id"],
    )
    return await get_application(pool, tenant_id=tenant_id, name=name)


def resolve_object_app_object_type(application: dict) -> Optional[str]:
    for surface in application["definition"].get("surfaces", []):
        if surface.get("type") == "objectApp":
            return surface["objectType"]
    return None


def resolve_analytics_object_type(application: dict) -> Optional[str]:
    """The **analytics** surface: a lightweight Contour/Code
    Workbook equivalent — ad-hoc pivot/aggregate exploration, scoped to
    one declared ObjectType per surface (`_referenced_object_types`
    already picks this up generically via its `objectType` key, same as
    `objectApp`, so dependency validation covers it for free). Unlike
    `objectApp`'s single fixed read path, an analytics surface doesn't
    pre-declare *which* query — `main.py`'s execute endpoint accepts any
    `ExecutionRequest`-shaped body at request time, bounded only to this
    declared ObjectType (and whatever Knowledge's own PDP allows for a
    `join` target).
    """
    for surface in application["definition"].get("surfaces", []):
        if surface.get("type") == "analytics":
            return surface["objectType"]
    return None


def resolve_agent_app_config(application: dict) -> Optional[dict]:
    """The declared `tools`/`systemPrompt`/`budget` an `agentApp` surface
    compiles into a session — already validated (real tool names, real
    budget shape) at draft/promote time, so `main.py`'s "run" endpoints
    can pass this straight through to Intelligence's `POST /sessions`.
    """
    surfaces = _agent_app_surfaces(application["definition"])
    return surfaces[0] if surfaces else None


def is_action_declared(application: dict, object_type: str, local_action_name: str) -> bool:
    full_name = f"{object_type}.{local_action_name}"
    return full_name in set(_referenced_actions(application["definition"]))


def get_dashboard_widgets(application: dict) -> list[dict]:
    for surface in application["definition"].get("surfaces", []):
        if surface.get("type") == "dashboard":
            return surface.get("widgets", [])
    return []


def get_form_surface(application: dict) -> Optional[dict]:
    forms = _form_surfaces(application["definition"])
    return forms[0] if forms else None


def validate_form_submission(form: dict, submitted: dict) -> None:
    """Runtime counterpart to `_validate_definition`'s form checks — the
    schema itself was already proven sound (declared action, valid field
    types) at draft/promote time; this checks one actual submission
    against it before the request is forwarded to Knowledge's real
    Action endpoint.
    """
    type_checks = {"string": str, "integer": int, "boolean": bool}
    for field in form.get("fields", []):
        name = field["name"]
        if field.get("required") and name not in submitted:
            raise FormValidationError(f"missing required field {name!r}")
        if name in submitted:
            expected_type = type_checks[field["type"]]
            if not isinstance(submitted[name], expected_type):
                raise FormValidationError(f"field {name!r} must be of type {field['type']!r}")
