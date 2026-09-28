import { useMemo, useState, type CSSProperties } from "react";
import { Link, useParams } from "@tanstack/react-router";
import ReactFlow, { Background, Controls, MarkerType, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { Button, HTMLSelect, Icon, InputGroup, Tag, type IconName } from "@blueprintjs/core";
import { useDatasetSchema, useLineageGraph } from "../../api/hooks";
import type { LineageGraphEdge, LineageNode } from "../../api/knowledge";
import { DetailPage, PageSection } from "../common/PageLayout";

type NodeKind = LineageNode["kind"];
type Direction = "both" | "upstream" | "downstream";

const KIND_META: Record<NodeKind, { icon: IconName; label: string; color: string; soft: string }> = {
  "object-type": { icon: "cube", label: "Object Type", color: "var(--hl-accent)", soft: "var(--hl-accent-soft)" },
  "dataset-version": { icon: "database", label: "Dataset Version", color: "#c9a227", soft: "rgba(201, 162, 39, 0.14)" },
  dataset: { icon: "th-list", label: "Dataset", color: "#4caf6a", soft: "rgba(76, 175, 106, 0.14)" },
  unknown: { icon: "flow-linear", label: "Entity", color: "var(--hl-text-muted)", soft: "var(--hl-bg-panel-raised)" },
};

type Selection = { kind: "node"; urn: string } | { kind: "edge"; info: LineageGraphEdge } | null;

function nodeMatches(node: LineageNode, query: string): boolean {
  if (!query) return true;
  const haystack = [
    node.display_name,
    node.urn,
    node.producer?.function_name,
    node.producer?.pipeline_name,
    ...node.columns.flatMap((column) => [column.source_column, column.target_property]),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function NodeLabel({ node }: { node: LineageNode }) {
  const meta = KIND_META[node.kind];
  return (
    <div title={node.urn}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
        <Icon icon={meta.icon} size={12} color={node.stale ? "var(--hl-warning)" : meta.color} />
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            color: node.stale ? "var(--hl-warning)" : meta.color,
            fontWeight: 600,
          }}
        >
          {meta.label}
          {node.focus ? " · current" : ""}
          {node.stale ? " · out of date" : ""}
        </span>
      </div>
      <div className="hl-mono" style={{ fontSize: 12, color: "var(--hl-text)", fontWeight: 500 }}>
        {node.display_name}
      </div>
    </div>
  );
}

function nodeStyle(node: LineageNode, dimmed: boolean): CSSProperties {
  const meta = KIND_META[node.kind];
  const color = node.stale ? "var(--hl-warning)" : meta.color;
  return {
    background: node.focus ? meta.soft : "var(--hl-bg-panel)",
    border: `1.5px solid ${node.focus || node.stale ? color : "var(--hl-border)"}`,
    borderRadius: 8,
    padding: "8px 12px",
    minWidth: 168,
    opacity: dimmed ? 0.35 : 1,
    boxShadow: node.focus ? `0 0 0 3px ${meta.soft}` : "var(--hl-shadow-sm)",
  };
}

function layoutNodes(graphNodes: LineageNode[], graphEdges: LineageGraphEdge[], focusUrn: string, query: string) {
  const hop = new Map<string, number>();
  hop.set(focusUrn, 0);
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, string[]>();
  for (const edge of graphEdges) {
    outgoing.set(edge.source_urn, [...(outgoing.get(edge.source_urn) ?? []), edge.target_urn]);
    incoming.set(edge.target_urn, [...(incoming.get(edge.target_urn) ?? []), edge.source_urn]);
  }
  const walk = (start: Map<string, string[]>, sign: number) => {
    const queue = [focusUrn];
    const seen = new Set([focusUrn]);
    while (queue.length) {
      const current = queue.shift() as string;
      for (const next of start.get(current) ?? []) {
        if (seen.has(next)) continue;
        seen.add(next);
        hop.set(next, (hop.get(current) ?? 0) + sign);
        queue.push(next);
      }
    }
  };
  walk(outgoing, 1);
  walk(incoming, -1);

  const columns = new Map<number, LineageNode[]>();
  for (const node of graphNodes) {
    const column = hop.get(node.urn) ?? 0;
    columns.set(column, [...(columns.get(column) ?? []), node]);
  }
  const nodes: Node[] = [];
  for (const [column, columnNodes] of columns) {
    columnNodes.forEach((node, index) => {
      nodes.push({
        id: node.urn,
        position: { x: column * 280, y: index * 110 },
        data: { label: <NodeLabel node={node} /> },
        style: nodeStyle(node, !nodeMatches(node, query)),
        className: "hl-lineage-node",
      });
    });
  }
  const flowEdges: Edge[] = graphEdges.map((edge, index) => ({
    id: `e${index}`,
    source: edge.source_urn,
    target: edge.target_urn,
    className: "hl-lineage-edge",
    label: edge.column_mappings.length
      ? `${edge.relation} · ${edge.column_mappings.length}`
      : edge.stale
        ? `${edge.relation} · stale`
        : edge.relation,
    labelStyle: { fill: edge.stale ? "var(--hl-warning)" : "var(--hl-text-muted)", fontSize: 11, fontWeight: 500 },
    labelBgStyle: { fill: "var(--hl-bg-panel)" },
    labelBgPadding: [6, 3] as [number, number],
    labelBgBorderRadius: 6,
    style: { stroke: edge.stale ? "var(--hl-warning)" : "var(--hl-border-strong)", strokeWidth: 1.5 },
    markerEnd: { type: MarkerType.ArrowClosed, color: edge.stale ? "var(--hl-warning)" : "var(--hl-border-strong)", width: 18, height: 18 },
  }));
  return { nodes, flowEdges };
}

export function LineagePage() {
  const { urn } = useParams({ from: "/shell/lineage/$urn" });
  const decodedUrn = decodeURIComponent(urn);
  const [depth, setDepth] = useState(4);
  const [direction, setDirection] = useState<Direction>("both");
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState<Selection>(null);
  const { data: graph } = useLineageGraph(decodedUrn, depth, direction);
  const needle = query.trim().toLowerCase();

  const { nodes, flowEdges } = useMemo(
    () => layoutNodes(graph?.nodes ?? [], graph?.edges ?? [], graph?.focus_urn ?? decodedUrn, needle),
    [graph, decodedUrn, needle],
  );
  const selectedNode = selection?.kind === "node" ? graph?.nodes.find((node) => node.urn === selection.urn) : undefined;
  const focus = graph?.nodes.find((node) => node.focus);

  return (
    <DetailPage breadcrumbs={[{ label: "Lineage" }]} title={focus?.display_name ?? "Lineage"}>
      <code className="hl-lineage-urn">{decodedUrn}</code>
      <div className="hl-lineage-toolbar">
        <InputGroup
          leftIcon="search"
          placeholder="Dataset, column, or function"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <HTMLSelect value={String(depth)} onChange={(event) => setDepth(Number(event.target.value))}>
          {[1, 2, 3, 4, 5, 6, 7, 8].map((value) => (
            <option key={value} value={value}>
              Depth {value}
            </option>
          ))}
        </HTMLSelect>
        <HTMLSelect value={direction} onChange={(event) => setDirection(event.target.value as Direction)}>
          <option value="both">Upstream and downstream</option>
          <option value="upstream">Ancestors</option>
          <option value="downstream">Descendants</option>
        </HTMLSelect>
        {graph?.truncated && <Tag intent="warning">Truncated</Tag>}
      </div>

      <div className="hl-lineage-layout">
        <div className="hl-lineage-canvas">
          <ReactFlow
            nodes={nodes}
            edges={flowEdges}
            fitView
            fitViewOptions={{ padding: 0.35 }}
            proOptions={{ hideAttribution: true }}
            onNodeClick={(_, node) => setSelection({ kind: "node", urn: node.id })}
            onEdgeClick={(_, edge) => {
              const info = graph?.edges[Number(edge.id.slice(1))];
              if (info) setSelection({ kind: "edge", info });
            }}
            onPaneClick={() => setSelection(null)}
          >
            <Background color="var(--hl-border)" gap={18} size={1} />
            <Controls />
          </ReactFlow>
        </div>

        <aside className="hl-lineage-sidebar">
          <PageSection title="Overview">
            <div className="hl-flex-row hl-gap-sm" style={{ flexWrap: "wrap" }}>
              <Stat value={graph?.nodes.length ?? 0} label="nodes" />
              <Stat value={graph?.edges.length ?? 0} label="edges" />
              <Stat value={graph?.nodes.filter((node) => node.stale).length ?? 0} label="out of date" />
            </div>
          </PageSection>

          <PageSection title="Details">
            {!selection && (
              <p className="hl-text-muted" style={{ fontSize: 12, margin: 0, lineHeight: 1.5 }}>
                Click a node or an edge. Depth walks ancestors and descendants from this URN.
              </p>
            )}
            {selectedNode && <NodeDetails node={selectedNode} />}
            {selection?.kind === "edge" && <EdgeDetails edge={selection.info} />}
          </PageSection>
        </aside>
      </div>
    </DetailPage>
  );
}

function NodeDetails({ node }: { node: LineageNode }) {
  const isDataset = node.kind === "dataset";
  const { data: schema, isLoading, isError } = useDatasetSchema(node.display_name, isDataset);
  const propertyByColumn = new Map(node.columns.map((column) => [column.source_column, column.target_property]));
  const schemaColumns = schema?.columns ?? [];

  return (
    <div className="hl-grid-gap-sm">
      <div className="hl-section-title">{node.display_name}</div>
      {node.stale && <Tag intent="warning">Input is newer than this build</Tag>}
      {node.expandable && <p className="hl-text-muted" style={{ fontSize: 12, margin: 0 }}>More hops exist. Raise depth.</p>}
      {node.built_at && (
        <div style={{ fontSize: 12 }}>
          Built <span className="hl-mono">{node.built_at}</span>
          {node.row_count !== null && <span> · {node.row_count} rows</span>}
        </div>
      )}
      {node.producer?.kind === "pipeline" && node.producer.pipeline_name && (
        <div style={{ fontSize: 12 }}>
          <Link to="/pipelines/$name" params={{ name: node.producer.pipeline_name }} className="hl-link-accent">
            {node.producer.pipeline_name}
          </Link>
          {node.producer.step_name && <span> / {node.producer.step_name}</span>}
          {node.producer.function_name && <div className="hl-mono">{node.producer.function_name}</div>}
        </div>
      )}
      {node.producer?.kind === "connector" && (
        <div className="hl-mono" style={{ fontSize: 11, wordBreak: "break-all" }}>
          {node.producer.connector_urn}
        </div>
      )}
      {isDataset && (
        <div>
          <div className="hl-section-title hl-mb-sm">Schema</div>
          {isLoading && <p className="hl-text-muted" style={{ fontSize: 12, margin: 0 }}>Loading columns…</p>}
          {isError && node.columns.length === 0 && (
            <p className="hl-text-muted" style={{ fontSize: 12, margin: 0 }}>No Iceberg schema for this dataset.</p>
          )}
          {schemaColumns.length > 0 && (
            <table style={{ width: "100%", fontSize: 11.5, borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th className="hl-text-muted" style={{ textAlign: "left", fontWeight: 500, paddingBottom: 6 }}>Column</th>
                  <th className="hl-text-muted" style={{ textAlign: "left", fontWeight: 500, paddingBottom: 6 }}>Type</th>
                  <th className="hl-text-muted" style={{ textAlign: "left", fontWeight: 500, paddingBottom: 6 }}>Property</th>
                </tr>
              </thead>
              <tbody>
                {schemaColumns.map((column) => (
                  <tr key={column.name} style={{ borderTop: "1px solid var(--hl-border)" }}>
                    <td className="hl-mono" style={{ padding: "5px 0" }}>{column.name}</td>
                    <td className="hl-mono" style={{ padding: "5px 0" }}>{column.type}</td>
                    <td className="hl-mono" style={{ padding: "5px 0" }}>{propertyByColumn.get(column.name) ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <Link to="/catalog" search={{ dataset: node.display_name }} className="hl-link-accent">
            Open in catalog
          </Link>
        </div>
      )}
      {!isDataset && node.columns.length > 0 && (
        <table style={{ width: "100%", fontSize: 11.5, borderCollapse: "collapse" }}>
          <thead>
            <tr>
              <th className="hl-text-muted" style={{ textAlign: "left", fontWeight: 500, paddingBottom: 6 }}>Column</th>
              <th className="hl-text-muted" style={{ textAlign: "left", fontWeight: 500, paddingBottom: 6 }}>Property</th>
            </tr>
          </thead>
          <tbody>
            {node.columns.map((column) => (
              <tr key={`${column.source_column}-${column.target_property}`} style={{ borderTop: "1px solid var(--hl-border)" }}>
                <td className="hl-mono" style={{ padding: "5px 0" }}>{column.source_column}</td>
                <td className="hl-mono" style={{ padding: "5px 0" }}>{column.target_property}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {node.versions.length > 0 && (
        <div>
          <div className="hl-section-title hl-mb-sm">Builds</div>
          {node.versions.map((version) => (
            <div key={version.urn} style={{ fontSize: 11.5, marginBottom: 4 }}>
              <Link to="/lineage/$urn" params={{ urn: version.urn }} className="hl-link-accent">
                {version.latest ? "latest" : "older"} · {version.row_count ?? "—"} rows
              </Link>
            </div>
          ))}
        </div>
      )}
      {!node.focus && (
        <Link to="/lineage/$urn" params={{ urn: node.urn }}>
          <Button small icon="flow-linear" fill>Center lineage here</Button>
        </Link>
      )}
    </div>
  );
}

function EdgeDetails({ edge }: { edge: LineageGraphEdge }) {
  return (
    <div>
      <div className="hl-section-title hl-mb-sm">
        {edge.relation}
        {edge.stale ? " · stale" : ""}
      </div>
      {edge.column_mappings.length === 0 ? (
        <p className="hl-text-muted" style={{ fontSize: 12, margin: 0 }}>Dataset-level edge — no column mapping.</p>
      ) : (
        <table style={{ width: "100%", fontSize: 11.5, borderCollapse: "collapse" }}>
          <tbody>
            {edge.column_mappings.map((column) => (
              <tr key={`${column.source_column}-${column.target_property}`} style={{ borderTop: "1px solid var(--hl-border)" }}>
                <td className="hl-mono" style={{ padding: "5px 0" }}>{column.source_column}</td>
                <td className="hl-mono" style={{ padding: "5px 0" }}>{column.target_property}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div className="hl-stat-chip">
      <span className="hl-stat-chip-value">{value}</span>
      <span className="hl-stat-chip-label">{label}</span>
    </div>
  );
}
