"""Struct value parsing / per-field column assembly (no FastAPI deps)."""

from __future__ import annotations

import json
from typing import Any


def project_struct_to_declared_fields(rule: dict, value: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields declared on the struct rule (drop undeclared JSON keys).

    Source datasets often carry extra keys in a JSON blob; Foundry maps the
    declared struct shape. Projection happens on read/assemble so explorers
    and Value Type checks see the ontology contract, not raw payload noise.
    """
    properties = rule.get("properties") or {}
    if not isinstance(properties, dict) or not properties:
        return dict(value)
    return {key: value[key] for key in properties if key in value}


def parse_struct_or_array(rule: dict, raw_value: Any) -> Any:
    """Parse a JSON-text (or already-parsed) struct/array backing value."""
    if raw_value is None:
        return None
    if isinstance(raw_value, (dict, list)):
        kind = rule.get("kind")
        if kind == "struct" and isinstance(raw_value, dict):
            return project_struct_to_declared_fields(rule, raw_value)
        if kind == "array" and isinstance(raw_value, list):
            return raw_value
        return raw_value
    if not isinstance(raw_value, str):
        return raw_value
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw_value
    kind = rule.get("kind")
    if kind == "struct" and isinstance(parsed, dict):
        return project_struct_to_declared_fields(rule, parsed)
    if kind == "array" and isinstance(parsed, list):
        return parsed
    return raw_value


def assemble_struct_value(rule: dict, row: dict, source_col: str | None) -> Any:
    """Build a struct dict from an optional JSON backing column plus per-field columns.

    Foundry maps each struct field to a dataset column; Holon keeps the
    JSON column as the base (when present) and overlays ``field.column``
    values from the same row. Returns ``None`` when nothing could be assembled.
    Undeclared keys from the JSON blob are dropped (schema projection).
    """
    base: dict[str, Any] = {}
    if source_col is not None and source_col in row:
        parsed = parse_struct_or_array(rule, row.get(source_col))
        if isinstance(parsed, dict):
            base = dict(parsed)
    for field_name, field_rule in (rule.get("properties") or {}).items():
        if not isinstance(field_rule, dict):
            continue
        col = field_rule.get("column")
        if not isinstance(col, str) or not col:
            continue
        if col in row and row[col] is not None:
            base[field_name] = row[col]
    projected = project_struct_to_declared_fields(rule, base) if base else {}
    return projected if projected else None
