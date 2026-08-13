"""Renders an `OntologySchema` (see `schema.py`) into typed Python —
one `@dataclass` per ObjectType (nested one-level `@dataclass` for a
`struct` property, `list[...]` for `array`), plus one typed function per
Action Type. Plain f-strings, no templating dependency — consistent
with the rest of this build's "stdlib where it's genuinely enough"
convention (`cli/holon.py`, HolonClient).

The generated module is self-contained except for `holon_sdk.HolonClient`
(already a real, shared dependency every generated caller needs anyway
to actually talk to the API) — it is not meant to be vendored away from
this repo, the same way none of this build's other generated/shared
code is.
"""

from __future__ import annotations

from .schema import (
    ActionTypeSchema,
    InterfaceTypeSchema,
    ObjectTypeSchema,
    OntologySchema,
    PropertyType,
    RelationTypeSchema,
    SharedPropertyType,
    ValueType,
)

_BASE_TYPE_TO_PYTHON = {
    "string": "str",
    "integer": "int",
    "double": "float",
    "boolean": "bool",
    # ISO-8601 strings, same as the wire shape — deliberately not parsed
    # into `datetime`: every HTTP layer in this build already sends/
    # receives them as plain strings, and parsing here would be the one
    # place that silently diverged from that.
    "date": "str",
    "timestamp": "str",
}


def _struct_class_name(object_type_name: str, property_name: str) -> str:
    return f"{object_type_name}_{property_name[0].upper()}{property_name[1:]}"


def _python_type_for(
    prop: PropertyType,
    value_types: dict[str, ValueType],
    shared_property_types: dict[str, SharedPropertyType],
    *,
    struct_class_name: str = "",
) -> str:
    if prop.kind == "value_type":
        return _BASE_TYPE_TO_PYTHON[value_types[prop.value_type].base_type]
    if prop.kind == "shared_property_type":
        # A Shared Property Type is a canonical name — either a Value
        # Type wrap, or a one-level struct field map (Foundry parity).
        spt = shared_property_types[prop.shared_property_type]
        if spt.struct_properties:
            return "dict"  # structural TypedDict would be nicer; keep simple
        if not spt.value_type:
            raise ValueError(f"shared property type {spt.api_name!r} has neither value_type nor struct_properties")
        return _BASE_TYPE_TO_PYTHON[value_types[spt.value_type].base_type]
    if prop.kind == "struct":
        return struct_class_name
    if prop.kind == "array":
        # `_emit_object_type` special-cases array-of-struct before ever
        # reaching here (it needs a *named* nested dataclass, not just a
        # type string) — this branch only ever sees a value_type/
        # shared_property_type element in practice.
        return f"list[{_python_type_for(prop.element, value_types, shared_property_types)}]"
    raise ValueError(f"unknown property_types kind: {prop.kind!r}")


def _field_comment(prop: PropertyType, shared_property_types: dict[str, SharedPropertyType]) -> str:
    """A Shared Property Type's `display_name`/`description` has no
    other place to surface in a dataclass field — an inline comment is
    the cheapest way generated code still carries that canonical
    meaning forward, instead of silently losing it in favor of the bare
    Python type.
    """
    if prop.kind != "shared_property_type":
        return ""
    spt = shared_property_types[prop.shared_property_type]
    text = spt.display_name + (f" — {spt.description}" if spt.description else "")
    return f"  # {text}"


def _emit_object_type(
    object_type: ObjectTypeSchema, value_types: dict[str, ValueType], shared_property_types: dict[str, SharedPropertyType]
) -> str:
    struct_classes: list[str] = []
    field_lines: list[str] = []

    for property_name in object_type.property_mapping:
        prop = object_type.property_types.get(property_name)
        if prop is None:
            field_lines.append(f"    {property_name}: Any = None")
            continue
        # A struct can appear as a top-level property or as an array's
        # element (a struct reducer's source column, e.g.
        # `TestPriorityTarget.segment`) — both need the same named nested
        # dataclass, since `_python_type_for`'s `struct` case has no name
        # of its own to fall back on.
        struct_element = prop if prop.kind == "struct" else (prop.element if prop.kind == "array" and prop.element.kind == "struct" else None)
        if struct_element is not None:
            class_name = _struct_class_name(object_type.name, property_name)
            nested_fields = "\n".join(
                f"    {name}: {_python_type_for(leaf, value_types, shared_property_types)}"
                f"{_field_comment(leaf, shared_property_types)}"
                for name, leaf in struct_element.properties.items()
            )
            struct_classes.append(f"@dataclass\nclass {class_name}:\n{nested_fields or '    pass'}\n")
            python_type = class_name if prop.kind == "struct" else f"list[{class_name}]"
            field_lines.append(f"    {property_name}: Optional[{python_type}] = None")
        else:
            python_type = _python_type_for(prop, value_types, shared_property_types)
            field_lines.append(f"    {property_name}: Optional[{python_type}] = None{_field_comment(prop, shared_property_types)}")

    body = "\n".join(field_lines) or "    pass"
    class_def = f'@dataclass\nclass {object_type.name}:\n    """{object_type.description}"""\n\n{body}\n'
    return "\n".join(struct_classes) + class_def


def _emit_action_function(action: ActionTypeSchema) -> str:
    # Every Action Type is declarative — the one generic
    # `/objects/{object_type}/{id}/actions/{full_name}` route (full
    # dotted `name`, `parameters` always present) is the only shape.
    url_action_name = action.name
    # Two different ObjectTypes can both declare, say, an "archive"
    # action — a bare `local_name` function would silently overwrite the
    # earlier one in Python (no error) and fail to compile at all in
    # TypeScript (`emit_typescript.py` has the same constraint).
    # Namespacing by `target_object_type` keeps every emitted function
    # name unique, same convention `_struct_class_name` already uses for
    # nested struct classes.
    function_name = f"{action.target_object_type}_{action.local_name}"
    typed_params = ", ".join(f"{p.name}: Any{'' if p.required else ' = None'}" for p in action.parameters)
    params_dict = ", ".join(f'"{p.name}": {p.name}' for p in action.parameters)
    signature_tail = f", {typed_params}" if typed_params else ""
    body_params = f"{{{params_dict}}}" if params_dict else "{}"
    body_literal = f'{{"reason": reason, "parameters": {body_params}}}'
    return (
        f"def {function_name}(client: HolonClient, knowledge_url: str, token: str, instance_id: str, "
        f"*, reason: str{signature_tail}) -> dict:\n"
        f'    """{action.description}"""\n'
        # `_status`/`_result`, not `status`/`result` — a declarative
        # Action's own parameter can itself be named "status", so the
        # HTTP response's own unpacking must use names no ontology
        # property or parameter could ever shadow.
        f'    _status, _result = client.request(\n'
        f'        "POST", f"{{knowledge_url}}/objects/{action.target_object_type}/{{instance_id}}/actions/{url_action_name}",\n'
        f"        token=token, body={body_literal},\n"
        f"    )\n"
        f"    if _status not in (200,):\n"
        f'        raise RuntimeError(f"{action.name} failed ({{_status}}): {{_result}}")\n'
        f"    return _result\n"
    )


def _safe_fn_token(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)


def _emit_link_accessors(relation: RelationTypeSchema) -> str:
    """Foundry-style link accessors: get / link / unlink on each side.

    `get` works for every storage kind. `link`/`unlink` only for
    foreign_key and only from the source (FK-holding) ObjectType.
    """
    blocks: list[str] = []
    # Forward accessor (from source OT)
    fwd = _safe_fn_token(f"{relation.source_object_type}_{relation.source_api_name}")
    blocks.append(
        f"def get_{fwd}(client: HolonClient, knowledge_url: str, token: str, instance_id: str) -> dict:\n"
        f'    """Traverse `{relation.name}` forward ({relation.source_api_name})."""\n'
        f'    _status, _result = client.request(\n'
        f'        "GET", f"{{knowledge_url}}/objects/{relation.source_object_type}/{{instance_id}}/links/{relation.source_api_name}",\n'
        f"        token=token,\n"
        f"    )\n"
        f"    if _status != 200:\n"
        f'        raise RuntimeError(f"get_{fwd} failed ({{_status}}): {{_result}}")\n'
        f"    return _result\n"
    )
    # Reverse accessor (from target OT)
    rev = _safe_fn_token(f"{relation.target_object_type}_{relation.target_api_name}")
    blocks.append(
        f"def get_{rev}(client: HolonClient, knowledge_url: str, token: str, instance_id: str) -> dict:\n"
        f'    """Traverse `{relation.name}` reverse ({relation.target_api_name})."""\n'
        f'    _status, _result = client.request(\n'
        f'        "GET", f"{{knowledge_url}}/objects/{relation.target_object_type}/{{instance_id}}/links/{relation.target_api_name}",\n'
        f"        token=token,\n"
        f"    )\n"
        f"    if _status != 200:\n"
        f'        raise RuntimeError(f"get_{rev} failed ({{_status}}): {{_result}}")\n'
        f"    return _result\n"
    )
    if relation.storage_kind == "foreign_key":
        blocks.append(
            f"def link_{fwd}(client: HolonClient, knowledge_url: str, token: str, instance_id: str, *, target_id: Any) -> dict:\n"
            f'    """Link write for `{relation.name}` (sets FK `{relation.source_property}`)."""\n'
            f'    _status, _result = client.request(\n'
            f'        "PUT", f"{{knowledge_url}}/objects/{relation.source_object_type}/{{instance_id}}/links/{relation.source_api_name}",\n'
            f'        token=token, body={{"target_id": target_id}},\n'
            f"    )\n"
            f"    if _status != 200:\n"
            f'        raise RuntimeError(f"link_{fwd} failed ({{_status}}): {{_result}}")\n'
            f"    return _result\n"
        )
        blocks.append(
            f"def unlink_{fwd}(client: HolonClient, knowledge_url: str, token: str, instance_id: str) -> dict:\n"
            f'    """Unlink for `{relation.name}` (clears FK `{relation.source_property}`)."""\n'
            f'    _status, _result = client.request(\n'
            f'        "DELETE", f"{{knowledge_url}}/objects/{relation.source_object_type}/{{instance_id}}/links/{relation.source_api_name}",\n'
            f"        token=token,\n"
            f"    )\n"
            f"    if _status != 200:\n"
            f'        raise RuntimeError(f"unlink_{fwd} failed ({{_status}}): {{_result}}")\n'
            f"    return _result\n"
        )
    return "\n\n".join(blocks)


def _ontology_interface_class_name(name: str, object_type_names: set[str]) -> str:
    safe = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in name)
    if safe in object_type_names:
        return f"{safe}_Interface"
    return safe


def _emit_ontology_interface(
    iface: InterfaceTypeSchema,
    value_types: dict[str, ValueType],
    shared_property_types: dict[str, SharedPropertyType],
    object_type_names: set[str],
) -> str:
    class_name = _ontology_interface_class_name(iface.name, object_type_names)
    field_lines = ["    _objectType: Optional[str] = None"]
    for prop_name in iface.required_properties:
        prop = iface.property_types.get(prop_name)
        if prop is None:
            field_lines.append(f"    {prop_name}: Any = None")
            continue
        py_type = _python_type_for(prop, value_types, shared_property_types)
        comment = _field_comment(prop, shared_property_types)
        field_lines.append(f"    {prop_name}: Optional[{py_type}] = None{comment}")
    doc_bits = [iface.description] if iface.description else []
    if iface.parent_interfaces:
        doc_bits.append(f"extends {', '.join(iface.parent_interfaces)}")
    doc = f'    """{" — ".join(doc_bits)}"""\n' if doc_bits else ""
    class_block = (
        f"@dataclass\n"
        f"class {class_name}:\n"
        f"{doc}"
        + "\n".join(field_lines)
        + "\n"
    )
    fn = _safe_fn_token(f"list_interface_{iface.name}")
    list_block = (
        f"def {fn}(client: HolonClient, knowledge_url: str, token: str) -> list[dict]:\n"
        f'    """Polymorphic instances of interface `{iface.name}`."""\n'
        f'    _status, _result = client.request(\n'
        f'        "GET", f"{{knowledge_url}}/interfaces/{iface.name}/objects",\n'
        f"        token=token,\n"
        f"    )\n"
        f"    if _status != 200:\n"
        f'        raise RuntimeError(f"{fn} failed ({{_status}}): {{_result}}")\n'
        f"    return _result\n"
    )
    return class_block + "\n\n" + list_block


def emit_python(schema: OntologySchema) -> str:
    header = (
        '"""Generated by `holon codegen python` — do not hand-edit.\n\n'
        "Typed ObjectType dataclasses, Ontology Interfaces, Action functions,\n"
        "and RelationType link accessors, mirroring the live ontology at\n"
        "generation time. Regenerate after any ontology change rather than\n"
        "editing this file.\n\"\"\"\n\n"
        "from __future__ import annotations\n\n"
        "from dataclasses import dataclass\n"
        "from typing import Any, Optional\n\n"
        "from holon_sdk import HolonClient\n\n\n"
    )
    object_type_names = {ot.name for ot in schema.object_types}
    object_type_blocks = "\n\n".join(
        _emit_object_type(ot, schema.value_types, schema.shared_property_types) for ot in schema.object_types
    )
    interface_blocks = "\n\n".join(
        _emit_ontology_interface(
            iface, schema.value_types, schema.shared_property_types, object_type_names,
        )
        for iface in schema.interface_types
    )
    action_blocks = "\n\n".join(_emit_action_function(action) for action in schema.action_types)
    link_blocks = "\n\n".join(_emit_link_accessors(rel) for rel in schema.relation_types)
    parts = [header, object_type_blocks]
    if interface_blocks:
        parts.extend(["\n\n\n", interface_blocks])
    if action_blocks:
        parts.extend(["\n\n\n", action_blocks])
    if link_blocks:
        parts.extend(["\n\n\n", link_blocks])
    return "".join(parts) + "\n"
