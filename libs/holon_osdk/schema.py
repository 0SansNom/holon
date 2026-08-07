"""Ontology schema introspection — the OSDK generator's one data
source. Walks Knowledge's live ontology surface over HTTP
(`GET /ontology`, `/ontology/{name}`, `/value-types`, `/actions`,
`/actions/{name}`) and builds a plain intermediate model (dataclasses,
no I/O of their own) that `emit_python.py`/`emit_typescript.py` both
render from — one schema walk, two independent renderers, neither
knowing anything about HTTP.

Uses `holon_sdk.HolonClient` (already stdlib-only, already the shared
request/auth helper every other client in this build consolidates on)
rather than adding `httpx`/`requests` as a new dependency just for this.

Scoped to what the ontology's own `property_types` schema actually
allows: `struct`/`array` nesting is hard-limited to exactly one level
server-side (`ontology/publishing.py`'s `_validate_property_types`) —
an array's `element` may only ever be a `value_type` leaf, never another
`struct`/`array`, so this model (and both emitters) never needs to
represent unbounded nesting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from holon_sdk import HolonClient


@dataclass
class ValueType:
    name: str
    base_type: str
    format_regex: Optional[str]


@dataclass
class SharedPropertyType:
    api_name: str
    display_name: str
    description: str
    value_type: str  # names a ValueType, resolved the same way a direct "value_type" leaf is


@dataclass
class PropertyType:
    kind: str  # "value_type" | "shared_property_type" | "struct" | "array"
    value_type: Optional[str] = None
    shared_property_type: Optional[str] = None
    properties: dict[str, "PropertyType"] = field(default_factory=dict)  # "struct" only
    element: Optional["PropertyType"] = None  # "array" only — always a value_type/shared_property_type leaf


@dataclass
class ObjectTypeSchema:
    name: str
    description: str
    property_mapping: dict[str, str]
    property_types: dict[str, PropertyType]


@dataclass
class ActionParameter:
    name: str
    value_type: str
    required: bool


@dataclass
class ActionTypeSchema:
    name: str
    local_name: str  # the part after "ObjectType." — what the emitted function is named
    target_object_type: str
    description: str
    parameters: list[ActionParameter]
    # A real asymmetry in the live API, not a generator quirk: the two
    # hardcoded Customer Actions are only ever reachable at
    # `/objects/Customer/{id}/actions/{local_name}` (their own specific
    # route, registered ahead of the generic one specifically so it
    # wins — see `routers/objects.py`'s module docstring); every
    # declarative Action Type instead goes through the one generic
    # `/objects/{object_type}/{id}/actions/{full_name}` route, which
    # requires the *full* dotted name. Both emitters need to know which
    # URL shape to generate per action.
    is_declarative: bool


@dataclass
class OntologySchema:
    object_types: list[ObjectTypeSchema]
    value_types: dict[str, ValueType]
    shared_property_types: dict[str, SharedPropertyType]
    action_types: list[ActionTypeSchema]


def _parse_property_type(raw: dict) -> PropertyType:
    kind = raw["kind"]
    if kind == "value_type":
        return PropertyType(kind="value_type", value_type=raw["value_type"])
    if kind == "shared_property_type":
        return PropertyType(kind="shared_property_type", shared_property_type=raw["shared_property_type"])
    if kind == "struct":
        return PropertyType(kind="struct", properties={k: _parse_property_type(v) for k, v in raw["properties"].items()})
    if kind == "array":
        return PropertyType(kind="array", element=_parse_property_type(raw["element"]))
    raise ValueError(f"unknown property_types kind: {kind!r}")


def fetch_schema(*, knowledge_url: str, token: str) -> OntologySchema:
    # `identity_url` is only ever used by `HolonClient.token_for` — this
    # generator takes an already-minted token from its caller (the CLI's
    # own cached session, same as every other `holon` subcommand), so an
    # empty placeholder is harmless here.
    client = HolonClient(identity_url="")

    status, names = client.request("GET", f"{knowledge_url}/ontology", token=token)
    if status != 200:
        raise RuntimeError(f"GET /ontology failed ({status}): {names}")

    object_types = []
    for entry in names:
        name = entry["name"]
        status, detail = client.request("GET", f"{knowledge_url}/ontology/{name}", token=token)
        if status != 200:
            raise RuntimeError(f"GET /ontology/{name} failed ({status}): {detail}")
        property_types = {k: _parse_property_type(v) for k, v in (detail.get("property_types") or {}).items()}
        object_types.append(ObjectTypeSchema(
            name=detail["name"], description=detail["description"],
            property_mapping=detail["property_mapping"], property_types=property_types,
        ))

    status, value_type_rows = client.request("GET", f"{knowledge_url}/value-types", token=token)
    if status != 200:
        raise RuntimeError(f"GET /value-types failed ({status}): {value_type_rows}")
    value_types = {
        row["name"]: ValueType(name=row["name"], base_type=row["base_type"], format_regex=row.get("format_regex"))
        for row in value_type_rows
    }

    status, shared_property_type_rows = client.request("GET", f"{knowledge_url}/shared-property-types", token=token)
    if status != 200:
        raise RuntimeError(f"GET /shared-property-types failed ({status}): {shared_property_type_rows}")
    shared_property_types = {
        row["api_name"]: SharedPropertyType(
            api_name=row["api_name"], display_name=row["display_name"],
            description=row.get("description", ""), value_type=row["value_type"],
        )
        for row in shared_property_type_rows
    }

    status, action_rows = client.request("GET", f"{knowledge_url}/actions", token=token)
    if status != 200:
        raise RuntimeError(f"GET /actions failed ({status}): {action_rows}")
    action_types = []
    for row in action_rows:
        status, detail = client.request("GET", f"{knowledge_url}/actions/{row['name']}", token=token)
        if status != 200:
            continue
        # A hardcoded Action's adapted shape (`actions._get_action_definition`)
        # never has a `parameters` key at all — only a declarative Action
        # Type's registry row does (empty list included). Presence, not
        # value, is the real signal.
        is_declarative = "parameters" in detail
        parameters = [
            ActionParameter(name=p["name"], value_type=p["value_type"], required=p.get("required", True))
            for p in detail.get("parameters", [])
        ]
        local_name = detail["name"].split(".", 1)[-1] if "." in detail["name"] else detail["name"]
        action_types.append(ActionTypeSchema(
            name=detail["name"], local_name=local_name, target_object_type=detail["target_object_type"],
            description=detail["description"], parameters=parameters, is_declarative=is_declarative,
        ))

    return OntologySchema(
        object_types=object_types, value_types=value_types,
        shared_property_types=shared_property_types, action_types=action_types,
    )
