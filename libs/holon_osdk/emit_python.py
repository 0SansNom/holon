"""Renders an `OntologySchema` (see `schema.py`) into typed Python —
one `@dataclass` per ObjectType (nested one-level `@dataclass` for a
`struct` property, `list[...]` for `array`), plus one typed function per
Action Type. Plain f-strings, no templating dependency — consistent
with the rest of this build's "stdlib where it's genuinely enough"
convention (`scripts/demo.py`, `cli/holon.py`).

The generated module is self-contained except for `holon_sdk.HolonClient`
(already a real, shared dependency every generated caller needs anyway
to actually talk to the API) — it is not meant to be vendored away from
this repo, the same way none of this build's other generated/shared
code is.
"""

from __future__ import annotations

from .schema import ActionTypeSchema, ObjectTypeSchema, OntologySchema, PropertyType, SharedPropertyType, ValueType

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
        # A Shared Property Type is just a canonical name for a Value
        # Type reference — the generated field's Python type resolves
        # through it exactly like a direct `value_type` leaf would.
        spt = shared_property_types[prop.shared_property_type]
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
    # The two hardcoded Customer Actions are only ever reachable at their
    # own specific route (`local_name` in the URL, no `parameters` in the
    # body — that endpoint's `ActionRequest` doesn't have the field); a
    # declarative Action Type goes through the one generic route instead
    # (full dotted `name`, `parameters` always present). See
    # `ActionTypeSchema.is_declarative`'s own docstring for why both
    # shapes are real, not a generator inconsistency.
    url_action_name = action.name if action.is_declarative else action.local_name
    # Two different ObjectTypes can both declare, say, an "archive"
    # action — a bare `local_name` function would silently overwrite the
    # earlier one in Python (no error) and fail to compile at all in
    # TypeScript (`emit_typescript.py` has the same constraint).
    # Namespacing by `target_object_type` keeps every emitted function
    # name unique, same convention `_struct_class_name` already uses for
    # nested struct classes.
    # The two hardcoded Actions are globally unique by construction (only
    # ever `putOnCreditHold`/`closeAccount`) and are kept at their bare,
    # familiar name; any declarative Action Type gets namespaced.
    function_name = action.local_name if not action.is_declarative else f"{action.target_object_type}_{action.local_name}"
    typed_params = ", ".join(f"{p.name}: Any{'' if p.required else ' = None'}" for p in action.parameters)
    params_dict = ", ".join(f'"{p.name}": {p.name}' for p in action.parameters)
    signature_tail = f", {typed_params}" if typed_params else ""
    if action.is_declarative:
        body_params = f"{{{params_dict}}}" if params_dict else "{}"
        body_literal = f'{{"reason": reason, "parameters": {body_params}}}'
    else:
        body_literal = '{"reason": reason}'
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


def emit_python(schema: OntologySchema) -> str:
    header = (
        '"""Generated by `holon codegen python` — do not hand-edit.\n\n'
        "Typed ObjectType dataclasses and Action functions, mirroring the\n"
        "live ontology at generation time. Regenerate after any ontology\n"
        "change rather than editing this file directly.\n\"\"\"\n\n"
        "from __future__ import annotations\n\n"
        "from dataclasses import dataclass\n"
        "from typing import Any, Optional\n\n"
        "from holon_sdk import HolonClient\n\n\n"
    )
    object_type_blocks = "\n\n".join(
        _emit_object_type(ot, schema.value_types, schema.shared_property_types) for ot in schema.object_types
    )
    action_blocks = "\n\n".join(_emit_action_function(action) for action in schema.action_types)
    return header + object_type_blocks + "\n\n\n" + action_blocks + "\n"
