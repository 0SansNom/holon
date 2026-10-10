"""Application definition validation for the Experience builder."""
from __future__ import annotations

import os

import asyncpg
import httpx

from . import ui_component_registry

_WORKSPACE_ID = os.environ.get("HOLON_WORKSPACE_ID", "main")


class InvalidApplicationDefinition(ValueError):
    pass


_VALID_FIELD_TYPES = {"string", "integer", "boolean"}
_VALID_BUDGET_KEYS = {"max_iterations", "max_tool_calls", "max_tokens"}


def _ontology_url(knowledge_url: str, path: str) -> str:
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{knowledge_url.rstrip('/')}/api/ontologies/{_WORKSPACE_ID}{suffix}"

def _holon_url(knowledge_url: str, path: str) -> str:
    suffix = path if path.startswith("/") else f"/{path}"
    return f"{knowledge_url.rstrip('/')}/api/holon{suffix}"

def _referenced_object_types(definition: dict) -> set[str]:
    types = {s["objectType"] for s in definition.get("surfaces", []) if "objectType" in s}
    types |= {b["objectType"] for b in definition.get("bindings", [])}
    for surface in definition.get("surfaces", []):
        if surface.get("type") == "dashboard":
            types |= {w["objectType"] for w in surface.get("widgets", []) if "objectType" in w}
    return types

def _referenced_relation_types(definition: dict) -> set[str]:
    """Optional link-type bindings on objectApp surfaces (api accessor names)."""
    names: set[str] = set()
    for surface in definition.get("surfaces", []):
        if surface.get("type") == "objectApp":
            for link in surface.get("links") or []:
                if isinstance(link, str) and link.strip():
                    names.add(link.strip())
    return names

def _referenced_object_sets(definition: dict) -> set[str]:
    """Optional Object Set bindings on objectApp / dashboard widgets."""
    names: set[str] = set()
    for surface in definition.get("surfaces", []):
        if surface.get("type") == "objectApp" and surface.get("objectSet"):
            names.add(surface["objectSet"])
        if surface.get("type") == "dashboard":
            for widget in surface.get("widgets", []):
                if widget.get("objectSet"):
                    names.add(widget["objectSet"])
    return names

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
    """Validate that application definition references valid ObjectTypes, Actions, components, and tools."""
    headers = {"Authorization": authorization}
    object_types = sorted(_referenced_object_types(definition))
    object_sets = sorted(_referenced_object_sets(definition))
    actions = sorted(_referenced_actions(definition))
    components = sorted(_referenced_components(definition))
    tools = sorted(_referenced_tools(definition))
    relation_link_names = sorted(_referenced_relation_types(definition))

    for object_type in object_types:
        response = await http.get(_ontology_url(knowledge_url, f"/objectTypes/{object_type}"), headers=headers)
        if response.status_code == 404:
            raise InvalidApplicationDefinition(f"unknown ObjectType {object_type!r}")
        response.raise_for_status()

    if relation_link_names:
        response = await http.get(_ontology_url(knowledge_url, "/linkTypes"), headers=headers)
        response.raise_for_status()
        relation_rows = response.json()
        known_accessors: set[str] = set()
        for row in relation_rows:
            local = str(row.get("name", "")).split(".", 1)[-1]
            known_accessors.add((row.get("source_api_name") or "").strip() or local)
            known_accessors.add((row.get("target_api_name") or "").strip() or row.get("target_property") or local)
            known_accessors.add(row.get("target_property") or "")
            known_accessors.add(local)
        unknown_links = [n for n in relation_link_names if n not in known_accessors]
        if unknown_links:
            raise InvalidApplicationDefinition(f"unknown link accessor(s) declared: {unknown_links}")
        for surface in definition.get("surfaces", []):
            if surface.get("type") != "objectApp" or not surface.get("links"):
                continue
            ot = surface.get("objectType") or ""
            for link in surface.get("links") or []:
                attached = False
                for row in relation_rows:
                    source = str(row.get("source_object_type_urn", "")).rsplit(":", 1)[-1]
                    target = str(row.get("target_object_type_urn", "")).rsplit(":", 1)[-1]
                    local = str(row.get("name", "")).split(".", 1)[-1]
                    fwd = (row.get("source_api_name") or "").strip() or local
                    rev = (row.get("target_api_name") or "").strip() or row.get("target_property") or local
                    if ot == source and link in (fwd, local):
                        attached = True
                    if ot == target and link in (rev, row.get("target_property"), local):
                        attached = True
                if not attached:
                    raise InvalidApplicationDefinition(
                        f"link {link!r} is not attached to ObjectType {ot!r}"
                    )

    for object_set in object_sets:
        response = await http.get(_ontology_url(knowledge_url, f"/objectSets/{object_set}"), headers=headers)
        if response.status_code == 404:
            raise InvalidApplicationDefinition(f"unknown Object Set {object_set!r}")
        response.raise_for_status()
        body = response.json()
        set_type = str(body.get("object_type_urn", "")).rsplit(":", 1)[-1]
        # Every objectSet binding must also declare a matching objectType so
        # dependency tracking and PDP type auth stay coherent.
        declared_types: set[str] = set()
        for surface in definition.get("surfaces", []):
            if surface.get("type") == "objectApp" and surface.get("objectSet") == object_set:
                declared_types.add(surface.get("objectType", ""))
            if surface.get("type") == "dashboard":
                for widget in surface.get("widgets", []):
                    if widget.get("objectSet") == object_set:
                        declared_types.add(widget.get("objectType", ""))
        for declared in declared_types:
            if declared and declared != set_type:
                raise InvalidApplicationDefinition(
                    f"Object Set {object_set!r} targets {set_type!r}, not {declared!r}"
                )

    for action in actions:
        response = await http.get(_holon_url(knowledge_url, f"/actions/{action}"), headers=headers)
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

    return {
        "objectTypes": object_types,
        "objectSets": object_sets,
        "actions": actions,
        "relationTypes": relation_link_names,
    }

