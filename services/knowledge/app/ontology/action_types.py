"""Action Type registry — named parameters (each a `value_type`
reference, validated at invocation), declarative `edits` (Foundry-style
rules: modify property, create/delete object, create/delete link), and
`submission_criteria` (simple comparisons against the target instance's
current state, evaluated before anything is requested or applied).

A simple upsert registry (like Interfaces/Markings/RelationTypes), not
versioned/branched like ObjectTypes — an Action Type's definition is
lower-stakes to iterate on than a schema, and every other registry in
this build already treats "definition" and "governed schema lifecycle"
as two different tiers.

`actions.py` is what actually *applies* an Action Type at request/apply
time (`_get_action_definition`'s fallback, `_apply_declarative_edits`,
`request_generic_action`) — this module only owns the registry.
"""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

from .type_classes import normalize_type_classes
from .lifecycle import normalize_deprecation_metadata
from ..action_structural import validate_edit_declaration, is_property_edit

_ALLOWED_OPERATORS = {"eq", "neq", "gt", "gte", "lt", "lte"}
_ALLOWED_RISK_LEVELS = {"low", "high"}
_ALLOWED_EDIT_SOURCES = {"parameter", "literal"}
# "value_type" (default, omittable): a scalar validated against a named
# Value Type. "object_reference": the submitted value must be a real
# instance id of the declared `object_type` — checked at invocation time
# (`declarative.request_generic_action`), not here, same "structural now,
# real references at invocation" split every other cross-reference in
# this module already follows.
_ALLOWED_PARAMETER_KINDS = {"value_type", "object_reference"}
_ALLOWED_PARAMETER_DEFAULT_KINDS = {"static", "current_object", "object_property"}
_ALLOWED_PRINCIPAL_FIELDS = {"urn", "type"}

# `actions.py`'s `_apply_now`/`approve_action` splat a declarative
# Action's edit results (`{property: newValue}`) straight into their own
# top-level response dict — an edit named e.g. "status" would silently
# overwrite the response's own "status" control field. Rejected at
# registration (top-level key only; nested paths use the first segment).
_RESERVED_RESPONSE_KEYS = {"status", "action", "riskLevel", "approvalId", "sagaStatus", "invocationId"}


def validate_submission_criterion(criterion: dict) -> None:
    """Structural check for Foundry-style submission criteria (P2b).

    Supported shapes (recursive):
    - leaf property: ``{property, operator, value, message?}``
    - leaf principal: ``{principal: urn|type, operator, value, message?}``
    - group: ``{all|any: [criterion, ...], message?}``
    """
    if not isinstance(criterion, dict):
        raise ValueError(f"malformed submission criterion: {criterion!r}")
    if "message" in criterion and criterion["message"] is not None and not isinstance(criterion["message"], str):
        raise ValueError("submission criterion message must be a string")

    if "all" in criterion or "any" in criterion:
        if ("all" in criterion) == ("any" in criterion):
            raise ValueError("submission criterion group must have exactly one of 'all' or 'any'")
        key = "all" if "all" in criterion else "any"
        children = criterion[key]
        if not isinstance(children, list) or not children:
            raise ValueError(f"submission criterion '{key}' must be a non-empty list")
        for child in children:
            validate_submission_criterion(child)
        return

    if "principal" in criterion:
        if criterion["principal"] not in _ALLOWED_PRINCIPAL_FIELDS:
            raise ValueError(
                f"submission criterion principal must be one of {sorted(_ALLOWED_PRINCIPAL_FIELDS)}, "
                f"got {criterion.get('principal')!r}"
            )
        op = criterion.get("operator")
        if op not in _ALLOWED_OPERATORS and op != "in":
            raise ValueError(
                f"submission criterion for principal has unknown operator {op!r}"
            )
        if "value" not in criterion:
            raise ValueError(f"malformed principal criterion: {criterion!r} (expected 'value')")
        if op == "in" and not isinstance(criterion["value"], list):
            raise ValueError("principal operator 'in' requires value to be a list")
        return

    op = criterion.get("operator")
    if op not in _ALLOWED_OPERATORS and op != "in":
        raise ValueError(
            f"submission criterion for {criterion.get('property')!r} has unknown operator "
            f"{op!r} (expected one of {sorted(_ALLOWED_OPERATORS | {'in'})})"
        )
    if "property" not in criterion or "value" not in criterion:
        raise ValueError(f"malformed submission criterion: {criterion!r} (expected 'property', 'operator', 'value')")
    if op == "in" and not isinstance(criterion["value"], list):
        raise ValueError("property operator 'in' requires value to be a list")


def validate_parameter_default(
    parameter: dict,
    *,
    earlier_object_reference_names: set[str],
) -> None:
    """Foundry Form defaults — structural check at Action Type registration.

    Kinds:
    - ``static``: fixed ``value``
    - ``current_object``: the Action's target instance id
    - ``object_property``: a property of ``object`` (``"current"`` or an
      earlier ``object_reference`` parameter name)
    """
    default = parameter.get("default")
    if default is None:
        return
    if not isinstance(default, dict):
        raise ValueError(
            f"parameter {parameter.get('name')!r}: default must be an object, got {default!r}"
        )
    kind = default.get("kind")
    if kind not in _ALLOWED_PARAMETER_DEFAULT_KINDS:
        raise ValueError(
            f"parameter {parameter.get('name')!r}: unknown default.kind {kind!r} "
            f"(expected one of {sorted(_ALLOWED_PARAMETER_DEFAULT_KINDS)})"
        )
    if kind == "static":
        if "value" not in default:
            raise ValueError(
                f"parameter {parameter.get('name')!r}: default.kind='static' requires 'value'"
            )
        return
    if kind == "current_object":
        return
    # object_property
    prop = default.get("property")
    if not prop or not isinstance(prop, str):
        raise ValueError(
            f"parameter {parameter.get('name')!r}: default.kind='object_property' requires non-empty 'property'"
        )
    source = default.get("object", "current")
    if not isinstance(source, str) or not source:
        raise ValueError(
            f"parameter {parameter.get('name')!r}: default.object must be 'current' or an object_reference parameter name"
        )
    if source != "current" and source not in earlier_object_reference_names:
        raise ValueError(
            f"parameter {parameter.get('name')!r}: default.object {source!r} must be 'current' or an "
            f"object_reference parameter declared earlier (Foundry order rule)"
        )


async def create_action_type(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    name: str,
    target_object_type: Optional[str] = None,
    target_interface: Optional[str] = None,
    required_permission: str,
    risk_level: str,
    description: str,
    parameters: list[dict],
    edits: list[dict],
    submission_criteria: Optional[list[dict]] = None,
    function_side_effect: Optional[str] = None,
    writeback_dataset: Optional[str] = None,
    edit_function: Optional[str] = None,
    sections: Optional[list[dict]] = None,
    type_classes: Optional[list[str]] = None,
    lifecycle_status: str = "experimental",
    deprecation_reason: Optional[str] = None,
    deprecation_deadline=None,
    replacement_urn: Optional[str] = None,
    notify_webhook: Optional[str] = None,
) -> dict:
    """Structural validation only — real references (a parameter's
    `value_type`, an edit's target property, `writeback_dataset` naming
    a real Connectivity `write_target`) are checked against live state
    at invocation time (`actions.request_generic_action`), not here: an
    Action Type may be registered before the ObjectType — or the write
    target — it references even exists yet, same "define now, validate
    against real state at the point of use" posture `interface_type`'s
    `required_properties` already has. `edit_function` follows the same
    posture as `function_side_effect` (also unchecked here, resolved
    only at invocation via `function_registry`) rather than being
    validated eagerly — kept consistent rather than introducing a
    second, stricter tier for one function-name field but not the other.

    `writeback_dataset` requires `risk_level == "high"` — the saga that
    actually performs a writeback (Automation's `consume_events`) only
    ever triggers for an *approved* invocation (`approval_id is not
    None`); a low-risk Action never creates one, so declaring a
    writeback target on one would silently never fire. Rejected here
    rather than accepted and quietly ignored.
    """
    if (target_object_type is None) == (target_interface is None):
        raise ValueError("exactly one of target_object_type or target_interface is required")
    if risk_level not in _ALLOWED_RISK_LEVELS:
        raise ValueError(f"unknown risk_level: {risk_level!r} (expected one of {sorted(_ALLOWED_RISK_LEVELS)})")
    if not description:
        raise ValueError("description is required")
    # Function-backed Actions (Object App inline logic that must compute
    # its own edits — see `edit_function`'s docstring at the DDL) declare
    # no static `edits` at all; a purely declarative Action Type still
    # needs at least one. Never both — a function's output would have no
    # defined precedence against a static list.
    if bool(edits) == bool(edit_function):
        raise ValueError("exactly one of edits or edit_function is required")
    if writeback_dataset is not None and risk_level != "high":
        raise ValueError("writeback_dataset requires risk_level='high' — a low-risk Action's saga never triggers")
    if notify_webhook is not None:
        if not isinstance(notify_webhook, str) or not notify_webhook.strip():
            raise ValueError("notify_webhook must be a non-empty URL when set")
        if not notify_webhook.strip().startswith(("http://", "https://")):
            raise ValueError("notify_webhook must be an http(s) URL")
        notify_webhook = notify_webhook.strip()

    classes = normalize_type_classes(type_classes)
    dep = normalize_deprecation_metadata(
        lifecycle_status,
        deprecation_reason=deprecation_reason,
        deprecation_deadline=deprecation_deadline,
        replacement_urn=replacement_urn,
    )

    parameter_names = set()
    earlier_object_refs: set[str] = set()
    for parameter in parameters:
        if "name" not in parameter:
            raise ValueError(f"malformed parameter declaration: {parameter!r} (expected 'name')")
        kind = parameter.get("kind", "value_type")
        if kind not in _ALLOWED_PARAMETER_KINDS:
            raise ValueError(f"parameter {parameter['name']!r}: unknown kind {kind!r} (expected one of {sorted(_ALLOWED_PARAMETER_KINDS)})")
        if kind == "value_type" and "value_type" not in parameter:
            raise ValueError(f"malformed parameter declaration: {parameter!r} (expected 'value_type')")
        if kind == "object_reference" and "object_type" not in parameter:
            raise ValueError(f"malformed parameter declaration: {parameter!r} (expected 'object_type' for kind='object_reference')")
        if kind == "object_reference" and parameter.get("object_set") is not None:
            if not isinstance(parameter["object_set"], str) or not parameter["object_set"].strip():
                raise ValueError(
                    f"parameter {parameter['name']!r}: object_set must be a non-empty Object Set name"
                )
            parameter["object_set"] = parameter["object_set"].strip()
        if kind != "object_reference" and parameter.get("object_set") is not None:
            raise ValueError(
                f"parameter {parameter['name']!r}: object_set is only valid on kind='object_reference'"
            )
        if "type_classes" in parameter:
            parameter["type_classes"] = normalize_type_classes(parameter.get("type_classes"))
        validate_parameter_default(parameter, earlier_object_reference_names=earlier_object_refs)
        parameter_names.add(parameter["name"])
        if kind == "object_reference":
            earlier_object_refs.add(parameter["name"])

    # A top-level property must be either fully replaced (plain edit) or
    # merged into (one or more dotted-path edits), never both — declarative.py's
    # _write_instance_edits collapses dotted paths onto a plain edit for the
    # same top-level key by insertion order, so whichever kind appears last
    # in `edits` silently wins and the other is discarded at apply time.
    # Caught here instead, at registration, before that ambiguity can ever
    # be applied against real data.
    top_property_kinds: dict[str, set[str]] = {}
    for edit in edits:
        validate_edit_declaration(edit, parameter_names=parameter_names)
        if is_property_edit(edit):
            top_property = edit["property"].split(".", 1)[0]
            kind = "dotted" if "." in edit["property"] else "plain"
            top_property_kinds.setdefault(top_property, set()).add(kind)
            if top_property in _RESERVED_RESPONSE_KEYS:
                raise ValueError(
                    f"edit property {edit['property']!r} collides with a reserved response field "
                    f"(one of {sorted(_RESERVED_RESPONSE_KEYS)}) — rename the property"
                )
            if edit["source"] not in _ALLOWED_EDIT_SOURCES:
                raise ValueError(
                    f"edit for {edit['property']!r} has unknown source {edit['source']!r} "
                    f"(expected one of {sorted(_ALLOWED_EDIT_SOURCES)})"
                )
    for top_property, kinds in top_property_kinds.items():
        if len(kinds) > 1:
            raise ValueError(
                f"edits mix a plain edit for {top_property!r} with a dotted-path edit into one of its "
                f"fields — pick one: either replace {top_property!r} wholesale, or only merge into its fields"
            )

    for criterion in submission_criteria or []:
        validate_submission_criterion(criterion)

    # Configure/Sections: purely a display grouping for the invocation
    # form (Foundry's "Sections") — structurally checked against the same
    # `parameter_names` set built above, never against live state, since
    # it never affects what gets submitted/applied.
    seen_in_section: set = set()
    for section in sections or []:
        if not section.get("name"):
            raise ValueError(f"malformed section: {section!r} (expected non-empty 'name')")
        for parameter_name in section.get("parameter_names", []):
            if parameter_name not in parameter_names:
                raise ValueError(f"section {section['name']!r} references undeclared parameter {parameter_name!r}")
            if parameter_name in seen_in_section:
                raise ValueError(f"parameter {parameter_name!r} appears in more than one section")
            seen_in_section.add(parameter_name)

    await pool.execute(
        """
        INSERT INTO action_type
            (tenant_id, name, target_object_type, target_interface, required_permission, risk_level, description,
             parameters, edits, submission_criteria, function_side_effect, writeback_dataset, edit_function, sections,
             type_classes, lifecycle_status, deprecation_reason, deprecation_deadline, replacement_urn, notify_webhook)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10::jsonb, $11, $12, $13, $14::jsonb, $15::jsonb,
                $16, $17, $18, $19, $20)
        ON CONFLICT (tenant_id, name) DO UPDATE SET
            target_object_type = EXCLUDED.target_object_type,
            target_interface = EXCLUDED.target_interface,
            required_permission = EXCLUDED.required_permission,
            risk_level = EXCLUDED.risk_level,
            description = EXCLUDED.description,
            parameters = EXCLUDED.parameters,
            edits = EXCLUDED.edits,
            submission_criteria = EXCLUDED.submission_criteria,
            function_side_effect = EXCLUDED.function_side_effect,
            writeback_dataset = EXCLUDED.writeback_dataset,
            edit_function = EXCLUDED.edit_function,
            sections = EXCLUDED.sections,
            type_classes = EXCLUDED.type_classes,
            lifecycle_status = EXCLUDED.lifecycle_status,
            deprecation_reason = EXCLUDED.deprecation_reason,
            deprecation_deadline = EXCLUDED.deprecation_deadline,
            replacement_urn = EXCLUDED.replacement_urn,
            notify_webhook = EXCLUDED.notify_webhook
        """,
        tenant_id, name, target_object_type, target_interface, required_permission, risk_level, description,
        json.dumps(parameters), json.dumps(edits), json.dumps(submission_criteria or []), function_side_effect,
        writeback_dataset, edit_function, json.dumps(sections or []), json.dumps(classes),
        dep["lifecycle_status"], dep["deprecation_reason"], dep["deprecation_deadline"], dep["replacement_urn"],
        notify_webhook,
    )
    return await get_action_type(pool, tenant_id, name)


def _parse_action_type_row(row: asyncpg.Record) -> dict:
    result = dict(row)
    for key in ("parameters", "edits", "submission_criteria", "sections", "type_classes"):
        if key in result and isinstance(result[key], str):
            result[key] = json.loads(result[key])
    if result.get("type_classes") is None:
        result["type_classes"] = []
    return result


async def get_action_type(pool: asyncpg.Pool, tenant_id: str, name: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM action_type WHERE tenant_id = $1 AND name = $2", tenant_id, name)
    return _parse_action_type_row(row) if row else None


async def list_action_types(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM action_type WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [_parse_action_type_row(row) for row in rows]


