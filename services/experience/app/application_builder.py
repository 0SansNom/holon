"""Experience Platform — Application Builder.

An Application Builder linked to the ontology:
all three surfaces are built (**object app**, **dashboard**, **form**).

- Applications only declare ObjectTypes/Actions that genuinely exist in Knowledge's ontology.
- Dependencies are computed automatically from bindings/actionRefs.
- Applications are versioned resources (draft -> promoted).
- Forms declare validation field schemas matching declared actions.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import asyncpg
import httpx

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
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


class InvalidApplicationDefinition(ValueError):
    pass


class FormValidationError(ValueError):
    """A form submission at runtime, not a definition problem — kept
    distinct from `InvalidApplicationDefinition` since it's raised on
    every submit, not just at draft/promote time.
    """


_VALID_FIELD_TYPES = {"string", "integer", "boolean"}


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


def _referenced_components(definition: dict) -> set[str]:
    components = {b["component"] for b in definition.get("bindings", []) if "component" in b}
    for surface in definition.get("surfaces", []):
        if surface.get("type") == "dashboard":
            components |= {w["component"] for w in surface.get("widgets", []) if "component" in w}
    return components


async def _validate_definition(
    pool: asyncpg.Pool, http: httpx.AsyncClient, *, knowledge_url: str, authorization: str, definition: dict
) -> dict:
    """An application can only declare ObjectTypes/Actions that genuinely
    exist in Knowledge's ontology — the set of things it's even allowed to
    bind to is bounded by the ontology API. Component names are checked the
    identical way, against the UI component plugin registry or built-in components.
    """
    headers = {"Authorization": authorization}
    object_types = sorted(_referenced_object_types(definition))
    actions = sorted(_referenced_actions(definition))
    components = sorted(_referenced_components(definition))

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
    name: str,
    definition: dict,
    knowledge_url: str,
    authorization: str,
) -> dict:
    dependencies = await _validate_definition(
        pool, http, knowledge_url=knowledge_url, authorization=authorization, definition=definition
    )
    existing = await get_application(pool, tenant_id=tenant_id, name=name)

    if existing is None:
        await pool.execute(
            "INSERT INTO application (tenant_id, name, version, definition, dependencies, status) "
            "VALUES ($1, $2, 1, $3::jsonb, $4::jsonb, 'draft')",
            tenant_id, name, json.dumps(definition), json.dumps(dependencies),
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
        await pool.execute(
            "INSERT INTO application (tenant_id, name, version, definition, dependencies, status) "
            "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, 'draft')",
            tenant_id, name, existing["version"] + 1, json.dumps(definition), json.dumps(dependencies),
        )

    return await get_application(pool, tenant_id=tenant_id, name=name)


async def promote(
    pool: asyncpg.Pool, http: httpx.AsyncClient, *, tenant_id: str, name: str, knowledge_url: str, authorization: str
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
        pool, http, knowledge_url=knowledge_url, authorization=authorization, definition=application["definition"]
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
