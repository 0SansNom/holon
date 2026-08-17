import { useMemo, useState, type CSSProperties } from "react";
import { Link, useParams } from "@tanstack/react-router";
import ReactFlow, { Background, Controls, MarkerType, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { Button, Icon, type IconName } from "@blueprintjs/core";
import { useLineage } from "../../api/hooks";
import { DetailPage, PageSection } from "../common/PageLayout";
import type { LineageEdge } from "../../api/knowledge";

type NodeKind = "object-type" | "dataset-version" | "dataset" | "unknown";

const KIND_META: Record<NodeKind, { icon: IconName; label: string; color: string; soft: string }> = {
  "object-type": { icon: "cube", label: "Object Type", color: "var(--hl-accent)", soft: "var(--hl-accent-soft)" },
  "dataset-version": { icon: "database", label: "Dataset Version", color: "#c9a227", soft: "rgba(201, 162, 39, 0.14)" },
  dataset: { icon: "th-list", label: "Dataset", color: "#4caf6a", soft: "rgba(76, 175, 106, 0.14)" },
  unknown: { icon: "flow-linear", label: "Entity", color: "var(--hl-text-muted)", soft: "var(--hl-bg-panel-raised)" },
};

function urnKind(urn: string): NodeKind {
  const parts = urn.split(":");
  const kind = parts[parts.length - 2];
  return kind === "object-type" || kind === "dataset-version" || kind === "dataset" ? kind : "unknown";
}

function shortName(urn: string): string {
  const parts = urn.split(":");
  const last = parts[parts.length - 1] ?? urn;
  return /^\d{6,}$/.test(last) ? `…${last.slice(-6)}` : last;
}

interface ColumnMapping {
  sourceColumn: string;
  targetProperty: string;
}

interface EdgeInfo {
  sourceUrn: string;
  targetUrn: string;
  relation: string;
  columnMappings: ColumnMapping[];
}

type Selection = { kind: "node"; urn: string } | { kind: "edge"; info: EdgeInfo } | null;

function NodeLabel({ urn, isRoot }: { urn: string; isRoot: boolean }) {
  const kind = urnKind(urn);
  const meta = KIND_META[kind];
  return (
    <div title={urn}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
        <Icon icon={meta.icon} size={12} color={meta.color} />
        <span
          style={{
            fontSize: 10,
            textTransform: "uppercase",
            letterSpacing: "0.04em",
            color: meta.color,
            fontWeight: 600,
          }}
        >
          {meta.label}
          {isRoot ? " · current" : ""}
        </span>
      </div>
      <div className="hl-mono" style={{ fontSize: 12, color: "var(--hl-text)", fontWeight: 500 }}>
        {shortName(urn)}
      </div>
    </div>
  );
}

function nodeStyle(kind: NodeKind, isRoot: boolean): CSSProperties {
  const meta = KIND_META[kind];
  return {
    background: isRoot ? meta.soft : "var(--hl-bg-panel)",
    border: `1.5px solid ${isRoot ? meta.color : "var(--hl-border)"}`,
    borderRadius: 8,
    padding: "8px 12px",
    minWidth: 168,
    boxShadow: isRoot ? `0 0 0 3px ${meta.soft}` : "var(--hl-shadow-sm)",
    transition: "box-shadow 0.15s ease, transform 0.15s ease",
  };
}

export function LineagePage() {
  const { urn } = useParams({ from: "/shell/lineage/$urn" });
  const decodedUrn = decodeURIComponent(urn);
  const { data: edges } = useLineage(decodedUrn);
  const [selection, setSelection] = useState<Selection>(null);

  const { nodes, flowEdges, edgeInfoById, presentKinds, propertyCount } = useMemo(() => {
    const nodeMap = new Map<string, Node>();
    const edgeGroups = new Map<string, EdgeInfo>();
    const kindsSeen = new Set<NodeKind>();
    let y = 0;

    function ensureNode(nodeUrn: string, x: number) {
      if (nodeMap.has(nodeUrn)) return;
      const kind = urnKind(nodeUrn);
      kindsSeen.add(kind);
      const isRoot = nodeUrn === decodedUrn;
      nodeMap.set(nodeUrn, {
        id: nodeUrn,
        position: { x, y: y++ * 96 },
        data: { label: <NodeLabel urn={nodeUrn} isRoot={isRoot} /> },
        style: nodeStyle(kind, isRoot),
        className: "hl-lineage-node",
      });
    }

    (edges ?? []).forEach((edge: LineageEdge) => {
      ensureNode(edge.source_urn, 0);
      ensureNode(edge.target_urn, 340);

      const key = `${edge.source_urn}|${edge.target_urn}|${edge.relation}`;
      let group = edgeGroups.get(key);
      if (!group) {
        group = { sourceUrn: edge.source_urn, targetUrn: edge.target_urn, relation: edge.relation, columnMappings: [] };
        edgeGroups.set(key, group);
      }
      if (edge.source_column) {
        group.columnMappings.push({ sourceColumn: edge.source_column, targetProperty: edge.target_property });
      }
    });

    const flowEdges: Edge[] = [];
    const edgeInfoById = new Map<string, EdgeInfo>();
    let propertyCount = 0;
    Array.from(edgeGroups.values()).forEach((info, i) => {
      const id = `e${i}`;
      edgeInfoById.set(id, info);
      propertyCount += info.columnMappings.length;
      const hasColumns = info.columnMappings.length > 0;
      flowEdges.push({
        id,
        source: info.sourceUrn,
        target: info.targetUrn,
        className: "hl-lineage-edge",
        label: hasColumns ? `${info.relation} · ${info.columnMappings.length} properties` : info.relation,
        labelStyle: { fill: "var(--hl-text-muted)", fontSize: 11, fontWeight: 500 },
        labelBgStyle: { fill: "var(--hl-bg-panel)" },
        labelBgPadding: [6, 3],
        labelBgBorderRadius: 6,
        style: { stroke: "var(--hl-border-strong)", strokeWidth: 1.5 },
        markerEnd: { type: MarkerType.ArrowClosed, color: "var(--hl-border-strong)", width: 18, height: 18 },
      });
    });

    return { nodes: Array.from(nodeMap.values()), flowEdges, edgeInfoById, presentKinds: kindsSeen, propertyCount };
  }, [edges, decodedUrn]);

  const rootKind = urnKind(decodedUrn);
  const objectTypeName = rootKind === "object-type" ? shortName(decodedUrn) : null;
  const fromCatalog = rootKind === "dataset-version" || rootKind === "dataset";
  const breadcrumbs = objectTypeName
    ? [
        { label: "Objects", to: "/objects" as const },
        { label: objectTypeName, to: "/objects/$type" as const, params: { type: objectTypeName } },
        { label: "Lineage" },
      ]
    : fromCatalog
      ? [{ label: "Catalog", to: "/catalog" as const }, { label: "Lineage" }]
      : [{ label: "Lineage" }];

  return (
    <DetailPage breadcrumbs={breadcrumbs} title="Lineage">
      <code className="hl-lineage-urn">{decodedUrn}</code>

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
              const info = edgeInfoById.get(edge.id);
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
              <Stat value={nodes.length} label={nodes.length === 1 ? "node" : "nodes"} />
              <Stat value={flowEdges.length} label={flowEdges.length === 1 ? "edge" : "edges"} />
              {propertyCount > 0 && <Stat value={propertyCount} label="properties mapped" />}
            </div>
            {objectTypeName && (
              <div className="hl-mt-sm">
                <Link to="/ontology/object-types/$name" params={{ name: objectTypeName }}>
                  <Button small icon="cog" fill>
                    Configure object type
                  </Button>
                </Link>
              </div>
            )}
          </PageSection>

          <PageSection title="Legend">
            <div className="hl-grid-gap-sm">
              {Array.from(presentKinds).map((kind) => {
                const meta = KIND_META[kind];
                return (
                  <div key={kind} className="hl-flex-row hl-items-center hl-gap-sm">
                    <Icon icon={meta.icon} size={12} color={meta.color} />
                    <span style={{ fontSize: 12, color: "var(--hl-text)" }}>{meta.label}</span>
                  </div>
                );
              })}
            </div>
          </PageSection>

          <PageSection title="Details">
            {!selection && (
              <p className="hl-text-muted" style={{ fontSize: 12, margin: 0, lineHeight: 1.5 }}>
                Click a node or an edge in the graph to inspect it here.
              </p>
            )}

            {selection?.kind === "node" && (
              <div>
                <div className="hl-section-title hl-mb-sm">{KIND_META[urnKind(selection.urn)].label}</div>
                <div className="hl-mono" style={{ fontSize: 11.5, color: "var(--hl-text)", wordBreak: "break-all" }}>
                  {selection.urn}
                </div>
                {urnKind(selection.urn) === "object-type" && (
                  <div className="hl-mt-sm">
                    <Link to="/ontology/object-types/$name" params={{ name: shortName(selection.urn) }}>
                      <Button small icon="cog" fill>
                        Configure object type
                      </Button>
                    </Link>
                  </div>
                )}
              </div>
            )}

            {selection?.kind === "edge" && (
              <div>
                <div className="hl-section-title hl-mb-sm">{selection.info.relation}</div>
                {selection.info.columnMappings.length === 0 ? (
                  <p className="hl-text-muted" style={{ fontSize: 12, margin: 0 }}>
                    Dataset-level edge — no column mapping.
                  </p>
                ) : (
                  <table style={{ width: "100%", fontSize: 11.5, borderCollapse: "collapse" }}>
                    <thead>
                      <tr>
                        <th className="hl-text-muted" style={{ textAlign: "left", fontWeight: 500, paddingBottom: 6 }}>
                          Column
                        </th>
                        <th className="hl-text-muted" style={{ textAlign: "left", fontWeight: 500, paddingBottom: 6 }}>
                          Property
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {selection.info.columnMappings.map((m) => (
                        <tr key={m.sourceColumn} style={{ borderTop: "1px solid var(--hl-border)" }}>
                          <td className="hl-mono" style={{ padding: "5px 0", color: "var(--hl-text)" }}>
                            {m.sourceColumn}
                          </td>
                          <td className="hl-mono" style={{ padding: "5px 0", color: "var(--hl-text)" }}>
                            {m.targetProperty}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </PageSection>
        </aside>
      </div>

      {(edges ?? []).length === 0 && (
        <p className="hl-text-muted hl-mt-md">No lineage edges recorded yet.</p>
      )}
    </DetailPage>
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
