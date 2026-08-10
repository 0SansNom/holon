"""Renders an `OntologySchema` (see `schema.py`) into typed TypeScript —
one `interface` per ObjectType (nested one-level `interface` for a
`struct` property, `Array<...>` for `array`), plus one typed function
per Action Type. Plain f-strings, no templating dependency, matching
`emit_python.py`'s own approach and this build's general
"stdlib/dependency-light where it's genuinely enough" convention.

Field names stay in the wire format's own snake_case rather than being
converted to camelCase — the same convention `services/experience/web/
src/api/knowledge.ts`'s hand-written interfaces already use, so this
generator's output looks like more of the same, not a second style.
"""

from __future__ import annotations

from .schema import ActionTypeSchema, ObjectTypeSchema, OntologySchema, PropertyType, SharedPropertyType, ValueType

_BASE_TYPE_TO_TS = {
    "string": "string",
    "integer": "number",
    "double": "number",
    "boolean": "boolean",
    # ISO-8601 strings — same "never silently parsed into a different
    # runtime shape than the wire format" reasoning as `emit_python.py`.
    "date": "string",
    "timestamp": "string",
}

# ObjectType names are user-chosen ontology strings, not reserved for this
# generator's benefit — an `interface {ObjectTypeName} {...}` that happens
# to share a name with one of TS's own global generic utility types
# shadows it for the rest of the *whole generated file*, breaking every
# other, unrelated `Record<...>`/`Array<...>` usage with a confusing
# "Type 'Record' is not generic" error far from the actual cause. Struct
# interface names (`_struct_interface_name`) are always namespaced with
# the property name and never hit this.
_RESERVED_TS_GLOBALS = {
    "Array", "Record", "Map", "Set", "Promise", "Date", "Object", "String",
    "Number", "Boolean", "Function", "Symbol", "RegExp", "Error", "JSON",
    "Math", "WeakMap", "WeakSet",
}


def _safe_interface_name(name: str) -> str:
    return f"{name}_" if name in _RESERVED_TS_GLOBALS else name


def _struct_interface_name(object_type_name: str, property_name: str) -> str:
    return f"{object_type_name}_{property_name[0].upper()}{property_name[1:]}"


def _ts_type_for(
    prop: PropertyType,
    value_types: dict[str, ValueType],
    shared_property_types: dict[str, SharedPropertyType],
    *,
    struct_interface_name: str = "",
) -> str:
    if prop.kind == "value_type":
        return _BASE_TYPE_TO_TS[value_types[prop.value_type].base_type]
    if prop.kind == "shared_property_type":
        # Resolves through to the wrapped Value Type's base type, same
        # as `emit_python.py`'s `_python_type_for`.
        spt = shared_property_types[prop.shared_property_type]
        return _BASE_TYPE_TO_TS[value_types[spt.value_type].base_type]
    if prop.kind == "struct":
        return struct_interface_name
    if prop.kind == "array":
        # `_emit_object_type` special-cases array-of-struct before ever
        # reaching here (it needs to emit a *named* nested interface, not
        # just a type string) — this branch only ever sees a
        # value_type/shared_property_type element in practice.
        return f"Array<{_ts_type_for(prop.element, value_types, shared_property_types)}>"
    raise ValueError(f"unknown property_types kind: {prop.kind!r}")


def _field_doc(prop: PropertyType, shared_property_types: dict[str, SharedPropertyType]) -> str:
    """Same reasoning as `emit_python.py`'s `_field_comment`: a Shared
    Property Type's `display_name`/`description` has to surface
    somewhere in the generated field, or it's lost.
    """
    if prop.kind != "shared_property_type":
        return ""
    spt = shared_property_types[prop.shared_property_type]
    text = spt.display_name + (f" — {spt.description}" if spt.description else "")
    return f"  /** {text} */\n"


def _emit_object_type(
    object_type: ObjectTypeSchema, value_types: dict[str, ValueType], shared_property_types: dict[str, SharedPropertyType]
) -> str:
    struct_interfaces: list[str] = []
    field_lines: list[str] = []

    for property_name in object_type.property_mapping:
        prop = object_type.property_types.get(property_name)
        if prop is None:
            field_lines.append(f"  {property_name}?: unknown;")
            continue
        # A struct can appear as a top-level property or as an array's
        # element (a struct reducer's source column, e.g.
        # `TestPriorityTarget.segment`) — both need the same named nested
        # interface, since `_ts_type_for`'s `struct` case has no name of
        # its own to fall back on.
        struct_element = prop if prop.kind == "struct" else (prop.element if prop.kind == "array" and prop.element.kind == "struct" else None)
        if struct_element is not None:
            interface_name = _struct_interface_name(object_type.name, property_name)
            nested_fields = "\n".join(
                f"{_field_doc(leaf, shared_property_types)}  {name}: {_ts_type_for(leaf, value_types, shared_property_types)};"
                for name, leaf in struct_element.properties.items()
            )
            struct_interfaces.append(f"export interface {interface_name} {{\n{nested_fields}\n}}\n")
            ts_type = interface_name if prop.kind == "struct" else f"Array<{interface_name}>"
            field_lines.append(f"  {property_name}?: {ts_type};")
        else:
            field_lines.append(
                f"{_field_doc(prop, shared_property_types)}  {property_name}?: {_ts_type_for(prop, value_types, shared_property_types)};"
            )

    body = "\n".join(field_lines)
    doc = f"/** {object_type.description} */\n" if object_type.description else ""
    interface_def = f"{doc}export interface {_safe_interface_name(object_type.name)} {{\n{body}\n}}\n"
    return "\n".join(struct_interfaces) + interface_def


def _emit_action_function(action: ActionTypeSchema) -> str:
    # Same URL/body asymmetry `emit_python.py` handles — see
    # `ActionTypeSchema.is_declarative`'s docstring.
    url_action_name = action.name if action.is_declarative else action.local_name
    # Two different ObjectTypes can both declare an action with the same
    # `local_name` (e.g. "archive") — a bare function name would be a
    # TypeScript compile error (`Cannot redeclare exported variable`),
    # confirmed by actually running `tsc --noEmit` against generated
    # output. The two hardcoded Actions are globally unique already and
    # keep their familiar bare name.
    function_name = action.local_name if not action.is_declarative else f"{action.target_object_type}_{action.local_name}"
    typed_params = ", ".join(f"{p.name}: unknown" for p in action.parameters)
    params_object = ", ".join(f"{p.name}" for p in action.parameters)
    signature_tail = f", {typed_params}" if typed_params else ""
    if action.is_declarative:
        body_params = f"{{ {params_object} }}" if params_object else "{}"
        body_literal = f"{{ reason, parameters: {body_params} }}"
    else:
        body_literal = "{ reason }"
    return (
        f"/** {action.description} */\n"
        f"export async function {function_name}(\n"
        f"  knowledgeUrl: string, token: string, instanceId: string, reason: string{signature_tail}\n"
        f"): Promise<Record<string, unknown>> {{\n"
        # Uses `_response` instead of `response` to avoid variable shadowing
        # if an Action parameter is named "response".
        f"  const _response = await fetch(\n"
        f'    `${{knowledgeUrl}}/objects/{action.target_object_type}/${{instanceId}}/actions/{url_action_name}`,\n'
        f"    {{\n"
        f'      method: "POST",\n'
        f"      headers: {{ \"Content-Type\": \"application/json\", Authorization: `Bearer ${{token}}` }},\n"
        f"      body: JSON.stringify({body_literal}),\n"
        f"    }},\n"
        f"  );\n"
        f"  if (!_response.ok) {{\n"
        f'    throw new Error(`{action.name} failed (${{_response.status}}): ${{await _response.text()}}`);\n'
        f"  }}\n"
        f"  return _response.json();\n"
        f"}}\n"
    )


def emit_typescript(schema: OntologySchema) -> str:
    header = (
        "/**\n"
        " * Generated by `holon codegen typescript` — do not hand-edit.\n"
        " *\n"
        " * Typed ObjectType interfaces and Action functions, mirroring the\n"
        " * live ontology at generation time. Regenerate after any ontology\n"
        " * change rather than editing this file directly.\n"
        " */\n\n"
    )
    object_type_blocks = "\n\n".join(
        _emit_object_type(ot, schema.value_types, schema.shared_property_types) for ot in schema.object_types
    )
    action_blocks = "\n\n".join(_emit_action_function(action) for action in schema.action_types)
    return header + object_type_blocks + "\n\n" + action_blocks + "\n"
