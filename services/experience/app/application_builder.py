"""Application Builder — Defines and promotes ontology-backed application configurations."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import httpx

from holon_common import build_urn

from . import ui_component_registry
from .application_validate import (  # noqa: F401
    InvalidApplicationDefinition,
    _agent_app_surfaces,
    _form_surfaces,
    _holon_url,
    _ontology_url,
    _referenced_actions,
    _referenced_components,
    _referenced_object_sets,
    _referenced_object_types,
    _referenced_relation_types,
    _referenced_tools,
    _validate_definition,
)

logger = logging.getLogger("experience.application_builder")

_WORKSPACE_ID = os.environ.get("HOLON_WORKSPACE_ID", "main")


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

class FormValidationError(ValueError):
    """A form submission at runtime, not a definition problem — kept
    distinct from `InvalidApplicationDefinition` since it's raised on
    every submit, not just at draft/promote time.
    """

_VALID_FIELD_TYPES = {"string", "integer", "boolean"}
_VALID_BUDGET_KEYS = {"max_iterations", "max_tool_calls", "max_tokens"}

async def list_applications(pool: asyncpg.Pool, *, tenant_id: str) -> list[dict]:
    """List latest versions of all applications for a tenant."""
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
    """Set or clear the project URN for an application."""
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
        await pool.execute(
            "UPDATE application SET definition = $1::jsonb, dependencies = $2::jsonb WHERE id = $3",
            json.dumps(definition), json.dumps(dependencies), existing["id"],
        )
    else:
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

def resolve_object_app_object_set(application: dict) -> Optional[str]:
    """Optional Object Set filter on the objectApp list surface."""
    for surface in application["definition"].get("surfaces", []):
        if surface.get("type") == "objectApp":
            name = surface.get("objectSet")
            return name if isinstance(name, str) and name else None
    return None

def resolve_analytics_object_type(application: dict) -> Optional[str]:
    """Resolve declared ObjectType for analytics surface."""
    for surface in application["definition"].get("surfaces", []):
        if surface.get("type") == "analytics":
            return surface["objectType"]
    return None

def resolve_agent_app_config(application: dict) -> Optional[dict]:
    """Resolve agentApp surface configuration (tools, system prompt, budget)."""
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

