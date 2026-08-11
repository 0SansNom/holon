"""Ontology health check — structural anti-pattern detection, from
Foundry's own "Ontology Design: Anti-patterns" documentation read in
full. Foundry documents these as a human checklist; nothing evidences
Ontology Manager detecting them automatically. Only the anti-patterns
with an honest, non-fuzzy signal get a real check here — System Silos,
Department Silos, Kitchen Sink, and Golden Hammer are deliberately
skipped (they need cross-system domain knowledge or runtime invocation
telemetry Holon doesn't collect; a guessed heuristic there would just
produce noise).

Lives here, not under `ontology/`, because the God Object check needs
real instance sampling via `core._type_handle`/`_resolve_many` — the
app layer, which `ontology/` never depends on (the same boundary
`core.py`'s own `_resolve_relation_neighbors` already respects).
"""

from __future__ import annotations

import re

from holon_common import Principal

from . import core, ontology

_ACTION_SPRAWL_THRESHOLD = 10
_GOD_OBJECT_PROPERTY_THRESHOLD = 15
_GOD_OBJECT_NULL_RATE_THRESHOLD = 0.5
_GOD_OBJECT_SAMPLE_SIZE = 50
_DRY_DUPLICATION_THRESHOLD = 0.7
_DRY_DUPLICATION_MIN_PROPERTIES = 3

# Foundry's own bad examples ("value", "quantity", "score", "type") plus
# the same class of unqualified generic noun ("item", "record", "data",
# "field") — deliberately excludes "status"/"date"/"name", which the
# doc's own good examples endorse in context (`age`, `status`).
_MISNOMER_PROPERTY_NAMES = {"value", "quantity", "score", "type", "item", "record", "data", "field"}
_MISNOMER_TYPE_NAMES = {"data", "item", "record"}

_VERSION_OR_YEAR_SUFFIX = re.compile(r"^(.+?)[\s_-]*(?:[vV]\d+|\d{4})$")


def _finding(kind: str, object_type: str, detail: str) -> dict:
    return {"kind": kind, "object_type": object_type, "severity": "warning", "detail": detail}


async def _check_action_sprawl(object_types: list[dict], action_types: list[dict]) -> list[dict]:
    """Count Action Types per ObjectType. Interface-scoped actions are
    attributed to every ObjectType that currently implements that
    interface (they are invokable there); if none do yet, they count
    against a synthetic `interface:{name}` key — never under `None`.
    """
    interface_to_ots: dict[str, list[str]] = {}
    for object_type in object_types:
        for interface_name in object_type.get("implements") or []:
            interface_to_ots.setdefault(interface_name, []).append(object_type["name"])

    counts: dict[str, int] = {}
    for action_type in action_types:
        target = action_type.get("target_object_type")
        target_interface = action_type.get("target_interface")
        if target:
            counts[target] = counts.get(target, 0) + 1
        elif target_interface:
            implementers = interface_to_ots.get(target_interface) or []
            if implementers:
                for name in implementers:
                    counts[name] = counts.get(name, 0) + 1
            else:
                key = f"interface:{target_interface}"
                counts[key] = counts.get(key, 0) + 1
        # else: malformed row (neither target) — skip rather than bucket under None

    findings = []
    for name, count in counts.items():
        if count <= _ACTION_SPRAWL_THRESHOLD:
            continue
        if name.startswith("interface:"):
            interface_name = name.removeprefix("interface:")
            findings.append(_finding(
                "action_sprawl", name,
                f"{count} Action Types target interface {interface_name!r} "
                f"(threshold: {_ACTION_SPRAWL_THRESHOLD}), with no implementing ObjectType yet — "
                f"consider bundling related edits into fewer, business-meaningful actions.",
            ))
        else:
            findings.append(_finding(
                "action_sprawl", name,
                f"{count} Action Types target this ObjectType (threshold: {_ACTION_SPRAWL_THRESHOLD}) — "
                f"consider bundling related edits into fewer, business-meaningful actions.",
            ))
    return findings


async def _check_god_object(object_types: list[dict], principal: Principal) -> list[dict]:
    findings = []
    for object_type in object_types:
        name = object_type["name"]
        property_mapping = object_type["property_mapping"]
        if len(property_mapping) <= _GOD_OBJECT_PROPERTY_THRESHOLD:
            continue
        handle = await core._type_handle(name)
        if handle is None:
            continue
        rows = await core._resolve_many(name, principal.tenant_id, handle["fetch_fn"], principal=principal)
        rows = rows[:_GOD_OBJECT_SAMPLE_SIZE]
        if not rows:
            continue
        columns = list(property_mapping.values())
        null_rate = sum(1 for row in rows for col in columns if row.get(col) is None) / (len(rows) * len(columns))
        if null_rate > _GOD_OBJECT_NULL_RATE_THRESHOLD:
            findings.append(_finding(
                "god_object", name,
                f"{len(property_mapping)} properties, {null_rate:.0%} null on average across "
                f"{len(rows)} sampled instances — consider splitting into focused, distinct ObjectTypes.",
            ))
    return findings


async def _check_misnomer(object_types: list[dict]) -> list[dict]:
    findings = []
    for object_type in object_types:
        name = object_type["name"]
        if name.lower() in _MISNOMER_TYPE_NAMES:
            findings.append(_finding(
                "misnomer_type", name,
                f"ObjectType name {name!r} is a generic noun — consider a domain-specific name.",
            ))
        for property_name in object_type["property_mapping"]:
            if property_name.lower() in _MISNOMER_PROPERTY_NAMES:
                findings.append(_finding(
                    "misnomer_property", name,
                    f"Property {property_name!r} is an unqualified generic name — "
                    f"consider a qualified name (e.g. monetaryValue instead of value).",
                ))
    return findings


async def _check_dry_duplication(object_types: list[dict]) -> list[dict]:
    findings = []
    for i, a in enumerate(object_types):
        a_props = set(a["property_mapping"])
        if len(a_props) < _DRY_DUPLICATION_MIN_PROPERTIES:
            continue
        for b in object_types[i + 1:]:
            b_props = set(b["property_mapping"])
            if len(b_props) < _DRY_DUPLICATION_MIN_PROPERTIES:
                continue
            overlap = len(a_props & b_props) / len(a_props | b_props)
            if overlap >= _DRY_DUPLICATION_THRESHOLD:
                shared = len(a_props & b_props)
                detail = (
                    f"Shares {shared}/{len(a_props | b_props)} properties with {b['name']!r} — "
                    f"consider a single canonical type or a shared interface."
                )
                findings.append(_finding("dry_duplication", a["name"], detail))
                findings.append(_finding(
                    "dry_duplication", b["name"],
                    f"Shares {shared}/{len(a_props | b_props)} properties with {a['name']!r} — "
                    f"consider a single canonical type or a shared interface.",
                ))
    return findings


async def _check_time_machine(object_types: list[dict]) -> list[dict]:
    bases: dict[str, list[str]] = {}
    for object_type in object_types:
        match = _VERSION_OR_YEAR_SUFFIX.match(object_type["name"])
        if match:
            bases.setdefault(match.group(1), []).append(object_type["name"])
    findings = []
    for base, names in bases.items():
        if len(names) < 2:
            continue
        for name in names:
            others = ", ".join(repr(n) for n in names if n != name)
            findings.append(_finding(
                "time_machine", name,
                f"Name suggests a versioned/dated copy, alongside {others} — "
                f"consider a single ObjectType with proper history/versioning instead.",
            ))
    return findings


async def _check_metadata_gaps(object_types: list[dict], relation_types: list[dict]) -> list[dict]:
    findings: list[dict] = []
    for object_type in object_types:
        mapping = object_type.get("property_mapping") or {}
        pk = object_type.get("primary_key") or "id"
        if pk not in mapping:
            findings.append(_finding(
                "missing_primary_key", object_type["name"],
                f"primary_key {pk!r} is not in property_mapping — instances may not resolve reliably.",
            ))
        if not object_type.get("title_key"):
            findings.append(_finding(
                "missing_title_key", object_type["name"],
                "No title_key configured — explorers fall back to primary_key/id.",
            ))
    for relation in relation_types:
        storage = relation.get("storage_kind") or "foreign_key"
        if relation.get("cardinality") == "many_to_many" and storage == "foreign_key":
            findings.append(_finding(
                "mn_without_join", relation["name"],
                "many_to_many declared with foreign_key storage — use join_dataset or object_backed.",
            ))
        if storage == "join_dataset" and not relation.get("join_dataset_urn"):
            findings.append(_finding(
                "join_dataset_incomplete", relation["name"],
                "storage_kind=join_dataset but join_dataset_urn is empty.",
            ))
        if storage == "object_backed" and not relation.get("mid_object_type_urn"):
            findings.append(_finding(
                "object_backed_incomplete", relation["name"],
                "storage_kind=object_backed but mid_object_type_urn is empty.",
            ))
    return findings


async def run_health_check(principal: Principal) -> list[dict]:
    """Orchestrates every check. Metadata-only checks (Action Sprawl,
    Misnomer, DRY duplication, Time Machine) run over the already-fetched
    listings; God Object additionally samples real instance data — the
    reason this whole module isn't just a cheap schema scan.
    """
    object_types = await ontology.list_object_types(core.pool, principal.tenant_id)
    action_types = await ontology.list_action_types(core.pool, principal.tenant_id)
    relation_types = await ontology.list_relation_types(core.pool, principal.tenant_id)

    findings: list[dict] = []
    findings += await _check_action_sprawl(object_types, action_types)
    findings += await _check_god_object(object_types, principal)
    findings += await _check_misnomer(object_types)
    findings += await _check_dry_duplication(object_types)
    findings += await _check_time_machine(object_types)
    findings += await _check_metadata_gaps(object_types, relation_types)
    return findings
