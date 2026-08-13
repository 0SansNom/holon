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
allows: `struct`/`array` nesting is hard-limited to one level deep
server-side (`ontology/publishing.py`'s `_validate_property_types`),
with a single named exception — an array's `element` may itself be a
`struct` ("struct array", e.g. a struct reducer's source column), whose
own fields are then leaves-only. Every other nested position stays
restricted to `value_type`/`shared_property_type`, so this model (and
both emitters) never needs to represent unbounded nesting.
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
    value_type: Optional[str] = None  # VT-wrapped SPT; null when struct-typed
    struct_properties: Optional[dict] = None  # Foundry-style struct SPT field map


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
    required: bool
    # "value_type" (default): `value_type` names a ValueType, `object_type`
    # is None. "object_reference": the reverse — the submitted value must
    # be a real instance id of `object_type`, structurally validated here
    # only; neither emitter currently distinguishes the two (both just
    # type the parameter as `Any`/`unknown`), but the schema itself needs
    # to survive walking an ontology that has one, not crash on it.
    kind: str = "value_type"
    value_type: Optional[str] = None
    object_type: Optional[str] = None


@dataclass
class ActionTypeSchema:
    name: str
    local_name: str  # the part after "ObjectType." — what the emitted function is named
    target_object_type: str
    description: str
    parameters: list[ActionParameter]


@dataclass
class RelationTypeSchema:
    """Foundry Link Type — bidirectional accessor names + storage kind."""

    name: str
    source_object_type: str
    target_object_type: str
    source_api_name: str
    target_api_name: str
    cardinality: str
    storage_kind: str
    source_property: str


@dataclass
class InterfaceTypeSchema:
    """Ontology Interface — checked polymorphic contract."""

    name: str
    description: str
    required_properties: list[str]
    required_actions: list[str]
    parent_interfaces: list[str] = field(default_factory=list)
    property_types: dict[str, PropertyType] = field(default_factory=dict)


@dataclass
class OntologySchema:
    object_types: list[ObjectTypeSchema]
    value_types: dict[str, ValueType]
    shared_property_types: dict[str, SharedPropertyType]
    action_types: list[ActionTypeSchema]
    relation_types: list[RelationTypeSchema] = field(default_factory=list)
    interface_types: list[InterfaceTypeSchema] = field(default_factory=list)


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
        # A top-level entry may be metadata-only — visibility/editable/
        # required/render_hints/type_classes with no `kind` at all
        # (`ontology/publishing.py`'s `_validate_property_types` allows
        # this explicitly). It contributes nothing structural to codegen,
        # so it's skipped here rather than passed to `_parse_property_type`
        # (which still requires `kind` for every entry it *does* see,
        # including every nested struct/array leaf — those are never
        # metadata-only server-side).
        property_types = {
            k: _parse_property_type(v)
            for k, v in (detail.get("property_types") or {}).items()
            if v.get("kind") is not None
        }
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
            api_name=row["api_name"],
            display_name=row["display_name"],
            description=row.get("description", ""),
            value_type=row.get("value_type"),
            struct_properties=row.get("struct_properties"),
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
        parameters = [
            ActionParameter(
                name=p["name"], required=p.get("required", True), kind=p.get("kind", "value_type"),
                value_type=p.get("value_type"), object_type=p.get("object_type"),
            )
            for p in detail.get("parameters", [])
        ]
        local_name = detail["name"].split(".", 1)[-1] if "." in detail["name"] else detail["name"]
        action_types.append(ActionTypeSchema(
            name=detail["name"], local_name=local_name, target_object_type=detail["target_object_type"],
            description=detail["description"], parameters=parameters,
        ))

    status, relation_rows = client.request("GET", f"{knowledge_url}/relation-types", token=token)
    if status != 200:
        raise RuntimeError(f"GET /relation-types failed ({status}): {relation_rows}")
    relation_types: list[RelationTypeSchema] = []
    for row in relation_rows:
        source_ot = str(row["source_object_type_urn"]).rsplit(":", 1)[-1]
        target_ot = str(row["target_object_type_urn"]).rsplit(":", 1)[-1]
        local = row["name"].split(".", 1)[-1]
        relation_types.append(
            RelationTypeSchema(
                name=row["name"],
                source_object_type=source_ot,
                target_object_type=target_ot,
                source_api_name=(row.get("source_api_name") or "").strip() or local,
                target_api_name=(row.get("target_api_name") or "").strip() or row.get("target_property") or local,
                cardinality=row.get("cardinality") or "many_to_one",
                storage_kind=row.get("storage_kind") or "foreign_key",
                source_property=row.get("source_property") or "",
            )
        )

    status, interface_rows = client.request("GET", f"{knowledge_url}/interfaces", token=token)
    if status != 200:
        raise RuntimeError(f"GET /interfaces failed ({status}): {interface_rows}")
    interface_types: list[InterfaceTypeSchema] = []
    for row in interface_rows:
        property_types = {
            k: _parse_property_type(v)
            for k, v in (row.get("property_types") or {}).items()
            if isinstance(v, dict) and v.get("kind") is not None
        }
        interface_types.append(
            InterfaceTypeSchema(
                name=row["name"],
                description=row.get("description") or "",
                required_properties=list(row.get("required_properties") or []),
                required_actions=list(row.get("required_actions") or []),
                parent_interfaces=list(row.get("parent_interfaces") or []),
                property_types=property_types,
            )
        )

    return OntologySchema(
        object_types=object_types, value_types=value_types,
        shared_property_types=shared_property_types, action_types=action_types,
        relation_types=relation_types,
        interface_types=interface_types,
    )
