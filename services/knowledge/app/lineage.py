"""Lineage graph management."""

from __future__ import annotations

import json
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional

import asyncpg

MAX_GRAPH_DEPTH = 8
DEFAULT_GRAPH_DEPTH = 4
MAX_GRAPH_NODES = 80

async def record_edge(
    conn: asyncpg.Connection,
    tenant_id: str,
    source_urn: str,
    target_urn: str,
    relation: str,
    *,
    source_column: str = "",
    target_property: str = "",
) -> None:
    await conn.execute(
        """
        INSERT INTO lineage_edge (tenant_id, source_urn, target_urn, relation, source_column, target_property)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (source_urn, target_urn, relation, source_column, target_property) DO NOTHING
        """,
        tenant_id,
        source_urn,
        target_urn,
        relation,
        source_column,
        target_property,
    )


async def edges_touching(pool: asyncpg.Pool, tenant_id: str, urn: str) -> list[dict]:
    """Edges touching `urn`, collapsed to the current state on the
    `target_urn = $2` side only.

    Every connector sync mints a fresh `dataset_version_urn` and records a
    brand-new `maps_to` edge from it to the (stable) ObjectType — by
    design, per this module's docstring, lineage is never hand-declared,
    only captured from execution. Nothing ever prunes the old edges, so a
    resynced-many-times dataset accumulates one dead source node per past
    sync. `(relation, source_column, target_property)` is the same slot
    across versions (this build maps each property from exactly one
    dataset's column, so two distinct live sources never share a slot),
    so keeping only the newest row per slot is a correctness-preserving
    view of "current lineage", not a heuristic — the full history stays
    queryable by `dataset_version_urn` directly (see `test_column_lineage.py`),
    just not repeated here as noise. The `source_urn = $2` side (e.g.
    walking a pipeline DAG downstream) has no such duplication and is
    left untouched.
    """
    rows = await pool.fetch(
        """
        WITH by_target AS (
            SELECT DISTINCT ON (relation, source_column, target_property)
                source_urn, target_urn, relation, source_column, target_property, created_at
            FROM lineage_edge
            WHERE tenant_id = $1 AND target_urn = $2
            ORDER BY relation, source_column, target_property, created_at DESC
        ),
        by_source AS (
            SELECT source_urn, target_urn, relation, source_column, target_property, created_at
            FROM lineage_edge
            WHERE tenant_id = $1 AND source_urn = $2
        )
        SELECT * FROM by_target
        UNION ALL
        SELECT * FROM by_source
        ORDER BY created_at
        """,
        tenant_id,
        urn,
    )
    return [dict(row) for row in rows]


def urn_kind(urn: str) -> str:
    parts = urn.split(":")
    kind = parts[-2] if len(parts) >= 2 else "unknown"
    return kind if kind in ("object-type", "dataset-version", "dataset") else "unknown"


def _as_dict(value) -> Optional[dict]:
    if value is None:
        return None
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    return dict(value) if isinstance(value, dict) else None


def _iso(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def assemble_graph(
    *,
    root: str,
    depth: int,
    direction: str,
    edges: list[dict],
    versions: list[dict],
    datasets: list[dict],
    object_types: list[dict],
) -> dict:
    """Current-state lineage: one node per dataset or object type.

    Dataset versions stay in the sidebar. A dataset is stale when the
    build that produced its representative version read an input that
    is no longer that input's latest version.
    """
    depth = max(1, min(depth, MAX_GRAPH_DEPTH))
    if direction not in ("both", "upstream", "downstream"):
        direction = "both"

    versions_by_urn = {v["urn"]: v for v in versions}
    versions_by_dataset: dict[str, list[dict]] = defaultdict(list)
    for version in versions:
        versions_by_dataset[version["dataset_urn"]].append(version)
    for group in versions_by_dataset.values():
        group.sort(key=lambda item: item["created_at"], reverse=True)
    latest = {dataset_urn: group[0]["urn"] for dataset_urn, group in versions_by_dataset.items() if group}
    dataset_names = {row["urn"]: row["display_name"] for row in datasets}
    type_names = {row["urn"]: row["name"] for row in object_types}

    focus_version = root if urn_kind(root) == "dataset-version" else None
    focus_dataset = None
    if focus_version and focus_version in versions_by_urn:
        focus_dataset = versions_by_urn[focus_version]["dataset_urn"]

    def dataset_of(version_urn: str) -> Optional[str]:
        version = versions_by_urn.get(version_urn)
        return version["dataset_urn"] if version else None

    def representative(dataset_urn: str) -> Optional[str]:
        if focus_dataset == dataset_urn and focus_version in versions_by_urn:
            return focus_version
        return latest.get(dataset_urn)

    def stable_id(raw: str) -> str:
        dataset = dataset_of(raw)
        if dataset:
            return dataset
        return raw

    by_source: dict[str, list[dict]] = defaultdict(list)
    by_target: dict[str, list[dict]] = defaultdict(list)
    for edge in edges:
        by_source[edge["source_urn"]].append(edge)
        by_target[edge["target_urn"]].append(edge)

    def edge_stale(edge: dict) -> bool:
        if edge["relation"] != "derived_from":
            return False
        source_dataset = dataset_of(edge["source_urn"])
        if source_dataset is None:
            return False
        return edge["source_urn"] != latest.get(source_dataset)

    def current_maps_to(object_type_urn: str) -> list[dict]:
        best: dict[tuple, dict] = {}
        for edge in by_target.get(object_type_urn, []):
            if edge["relation"] != "maps_to":
                continue
            source_dataset = dataset_of(edge["source_urn"])
            if focus_dataset and source_dataset == focus_dataset and edge["source_urn"] != focus_version:
                continue
            key = (source_dataset or edge["source_urn"], edge.get("source_column") or "", edge.get("target_property") or "")
            previous = best.get(key)
            if previous is None or edge.get("created_at", "") >= previous.get("created_at", ""):
                best[key] = edge
        return list(best.values())

    def adjacent(node_id: str) -> list[tuple[str, dict]]:
        found: list[tuple[str, dict]] = []
        kind = urn_kind(node_id)
        walk_up = direction in ("both", "upstream")
        walk_down = direction in ("both", "downstream")

        if kind == "object-type" and walk_up:
            grouped: dict[str, list[dict]] = defaultdict(list)
            for edge in current_maps_to(node_id):
                grouped[stable_id(edge["source_urn"])].append(edge)
            for source_id, group in grouped.items():
                found.append((source_id, _merged_edge(group)))

        if kind == "dataset" or node_id in versions_by_dataset:
            rep = representative(node_id)
            if rep is None:
                return found
            if walk_down:
                derived = [edge for edge in by_source.get(rep, []) if edge["relation"] == "derived_from"]
                for edge in derived:
                    found.append((stable_id(edge["target_urn"]), _merged_edge([edge])))
                maps: dict[str, list[dict]] = defaultdict(list)
                for edge in by_source.get(rep, []):
                    if edge["relation"] == "maps_to":
                        maps[edge["target_urn"]].append(edge)
                for target_id, group in maps.items():
                    found.append((target_id, _merged_edge(group)))
            if walk_up:
                for edge in by_target.get(rep, []):
                    if edge["relation"] == "derived_from":
                        found.append((stable_id(edge["source_urn"]), _merged_edge([edge])))
        return found

    start = stable_id(root)
    seen: dict[str, int] = {start: 0}
    queue: deque[str] = deque([start])
    kept: dict[tuple, dict] = {}
    truncated = False
    while queue:
        node_id = queue.popleft()
        hop = seen[node_id]
        if hop >= depth:
            continue
        for neighbor, edge in adjacent(node_id):
            if neighbor not in seen and len(seen) >= MAX_GRAPH_NODES:
                truncated = True
                continue
            key = (edge["source_urn"], edge["target_urn"], edge["relation"])
            kept[key] = edge
            if neighbor not in seen:
                seen[neighbor] = hop + 1
                queue.append(neighbor)

    nodes = [_node_payload(
        node_id,
        focus=node_id == start,
        versions_by_dataset=versions_by_dataset,
        versions_by_urn=versions_by_urn,
        latest=latest,
        dataset_names=dataset_names,
        type_names=type_names,
        representative=representative,
        edges=list(kept.values()),
        edge_stale=edge_stale,
        expandable=_expandable(node_id, seen, depth, adjacent),
    ) for node_id in seen]
    return {
        "root": root,
        "focus_urn": start,
        "depth": depth,
        "direction": direction,
        "truncated": truncated,
        "nodes": nodes,
        "edges": list(kept.values()),
    }


def _merged_edge(group: list[dict]) -> dict:
    head = group[0]
    mappings = [
        {"source_column": edge.get("source_column") or "", "target_property": edge.get("target_property") or ""}
        for edge in group
        if edge.get("source_column")
    ]
    source = head["source_urn"]
    target = head["target_urn"]
    return {
        "source_urn": source,
        "target_urn": target,
        "relation": head["relation"],
        "stale": False,
        "column_mappings": mappings,
        "_raw_edges": group,
    }


def _expandable(node_id: str, seen: dict[str, int], depth: int, adjacent) -> bool:
    if seen.get(node_id, 0) < depth:
        return False
    return any(neighbor not in seen for neighbor, _edge in adjacent(node_id))


def _node_payload(
    node_id: str,
    *,
    focus: bool,
    versions_by_dataset,
    versions_by_urn,
    latest,
    dataset_names,
    type_names,
    representative,
    edges,
    edge_stale,
    expandable: bool,
) -> dict:
    kind = urn_kind(node_id)
    rep = representative(node_id) if kind == "dataset" or node_id in versions_by_dataset else None
    version = versions_by_urn.get(rep) if rep else None
    columns = []
    stale = False
    for edge in edges:
        raws = edge.get("_raw_edges") or [edge]
        source_dataset = _edge_source_dataset(edge, versions_by_urn)
        target_dataset = _edge_target_dataset(edge, versions_by_urn)
        if edge["relation"] == "maps_to" and source_dataset == node_id:
            columns.extend(edge.get("column_mappings") or [])
        if edge["relation"] == "derived_from" and target_dataset == node_id:
            stale = stale or any(edge_stale(raw) for raw in raws)
    history = []
    for item in (versions_by_dataset.get(node_id) or [])[:8]:
        history.append({
            "urn": item["urn"],
            "created_at": _iso(item["created_at"]),
            "row_count": item.get("row_count"),
            "latest": item["urn"] == latest.get(node_id),
        })
    display = dataset_names.get(node_id) or type_names.get(node_id) or node_id.rsplit(":", 1)[-1]
    producer = _as_dict(version.get("producer")) if version else None
    return {
        "urn": node_id,
        "kind": kind if kind != "unknown" else ("dataset" if node_id in versions_by_dataset else "unknown"),
        "display_name": display,
        "focus": focus,
        "stale": stale,
        "expandable": expandable,
        "built_at": _iso(version.get("created_at")) if version else None,
        "row_count": version.get("row_count") if version else None,
        "producer": producer,
        "columns": columns,
        "versions": history,
        "opened_version_urn": rep if focus and urn_kind(rep or "") == "dataset-version" else None,
        "opened_is_latest": (rep == latest.get(node_id)) if rep else None,
    }


def _edge_source_dataset(edge: dict, versions_by_urn: dict) -> Optional[str]:
    for raw in edge.get("_raw_edges") or [edge]:
        version = versions_by_urn.get(raw["source_urn"])
        if version:
            return version["dataset_urn"]
    return None


def _edge_target_dataset(edge: dict, versions_by_urn: dict) -> Optional[str]:
    for raw in edge.get("_raw_edges") or [edge]:
        version = versions_by_urn.get(raw["target_urn"])
        if version:
            return version["dataset_urn"]
    return None


async def graph(pool: asyncpg.Pool, tenant_id: str, urn: str, *, depth: int = DEFAULT_GRAPH_DEPTH, direction: str = "both") -> dict:
    """Walk the tenant lineage and collapse it to the current dataset graph."""
    edge_rows = await pool.fetch(
        """
        SELECT source_urn, target_urn, relation, source_column, target_property, created_at
        FROM lineage_edge
        WHERE tenant_id = $1
        ORDER BY created_at
        """,
        tenant_id,
    )
    version_rows = await pool.fetch(
        """
        SELECT urn, dataset_urn, row_count, created_at, producer
        FROM dataset_version
        WHERE tenant_id = $1
        """,
        tenant_id,
    )
    dataset_rows = await pool.fetch(
        "SELECT urn, display_name FROM dataset WHERE tenant_id = $1",
        tenant_id,
    )
    type_rows = await pool.fetch(
        "SELECT urn, name FROM object_type WHERE tenant_id = $1",
        tenant_id,
    )
    assembled = assemble_graph(
        root=urn,
        depth=depth,
        direction=direction,
        edges=[dict(row) for row in edge_rows],
        versions=[dict(row) for row in version_rows],
        datasets=[dict(row) for row in dataset_rows],
        object_types=[dict(row) for row in type_rows],
    )
    versions_by_urn = {row["urn"]: dict(row) for row in version_rows}
    public_edges = []
    for edge in assembled["edges"]:
        raws = edge.get("_raw_edges") or []
        stale = False
        if edge["relation"] == "derived_from":
            for raw in raws:
                source_version = versions_by_urn.get(raw["source_urn"])
                if source_version is None:
                    continue
                dataset_urn = source_version["dataset_urn"]
                latest_urn = None
                latest_at = None
                for candidate in version_rows:
                    if candidate["dataset_urn"] != dataset_urn:
                        continue
                    if latest_at is None or candidate["created_at"] >= latest_at:
                        latest_at = candidate["created_at"]
                        latest_urn = candidate["urn"]
                if latest_urn is not None and raw["source_urn"] != latest_urn:
                    stale = True
        source = edge["source_urn"]
        target = edge["target_urn"]
        source_version = versions_by_urn.get(source)
        if source_version is not None:
            source = source_version["dataset_urn"]
        if edge["relation"] == "derived_from":
            target_version = versions_by_urn.get(target)
            if target_version is not None:
                target = target_version["dataset_urn"]
        public_edges.append({
            "source_urn": source,
            "target_urn": target,
            "relation": edge["relation"],
            "stale": stale,
            "column_mappings": edge.get("column_mappings") or [],
        })
    assembled["edges"] = public_edges
    return assembled
