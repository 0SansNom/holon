import { useMemo, useState, type CSSProperties } from "react";
import { useParams } from "@tanstack/react-router";
import ReactFlow, { Background, Controls, MarkerType, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { Icon, type IconName } from "@blueprintjs/core";
import { useLineage } from "../../api/hooks";
import { PageBreadcrumbs } from "../common/PageBreadcrumbs";
import type { LineageEdge } from "../../api/knowledge";

// Page-local light palette, predating the app-wide light theme migration
// (theme.css/main.tsx are light everywhere now, no `.bp6-dark` ancestor
// left to clash with) — kept as its own constants rather than switched
// to the global `--hl-*` CSS variables since this page's graph styling
// (KIND_META colors, edge/node canvas) needs values in JS, not just CSS.
const LIGHT = {
  bg: "#f6f7f9",
  panel: "#ffffff",
  border: "#e1e4ea",
  borderStrong: "#c7cdd8",
  text: "#1c2127",
  textMuted: "#5f6b7a",
  accent: "#2d63c8",
  danger: "#b23a48",
};

type NodeKind = "object-type" | "dataset-version" | "dataset" | "unknown";

const KIND_META: Record<NodeKind, { icon: IconName; label: string; color: string; soft: string }> = {
  "object-type": { icon: "cube", label: "Object Type", color: "#2d63c8", soft: "#eaf1fd" },
  "dataset-version": { icon: "database", label: "Dataset Version", color: "#8f6a1f", soft: "#fbf1de" },
  dataset: { icon: "th-list", label: "Dataset", color: "#3a8a4a", soft: "#e9f6ec" },
  unknown: { icon: "flow-linear", label: "Entity", color: "#5f6b7a", soft: "#f0f1f4" },
};

function urnKind(urn: string): NodeKind {
  const parts = urn.split(":");
  const kind = parts[parts.length - 2];
  return kind === "object-type" || kind === "dataset-version" || kind === "dataset" ? kind : "unknown";
}

function shortName(urn: string): string {
  const parts = urn.split(":");
  const last = parts[parts.length - 1] ?? urn;
  // dataset-version URNs end in a long numeric snapshot id — not
  // meaningful to a reader at a glance, so show a short suffix instead
  // of overflowing the node card; the full URN is always in the details
  // panel and the title attribute.
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
        <span style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em", color: meta.color, fontWeight: 600 }}>
          {meta.label}
          {isRoot ? " · current" : ""}
        </span>
      </div>
      <div style={{ fontFamily: "SFMono-Regular, Consolas, Menlo, monospace", fontSize: 12, color: LIGHT.text, fontWeight: 500 }}>
        {shortName(urn)}
      </div>
    </div>
  );
}

function nodeStyle(kind: NodeKind, isRoot: boolean): CSSProperties {
  const meta = KIND_META[kind];
  return {
    background: isRoot ? meta.soft : LIGHT.panel,
    border: `1.5px solid ${isRoot ? meta.color : LIGHT.border}`,
    borderRadius: 8,
    padding: "8px 12px",
    minWidth: 168,
    boxShadow: isRoot ? `0 0 0 3px ${meta.soft}` : "0 1px 2px rgba(16, 22, 34, 0.06)",
    transition: "box-shadow 0.15s ease, transform 0.15s ease",
  };
}

function sectionLabel(): CSSProperties {
  return { fontSize: 11, textTransform: "uppercase", letterSpacing: "0.04em", color: LIGHT.textMuted, fontWeight: 600 };
}

function LoadingState() {
  return (
    <div style={{ background: LIGHT.bg, margin: -24, padding: 24, minHeight: "100%" }}>
      <div
        style={{
          height: 560,
          background: LIGHT.panel,
          border: `1px solid ${LIGHT.border}`,
          borderRadius: 8,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          color: LIGHT.textMuted,
          fontSize: 13,
        }}
      >
        Loading lineage…
      </div>
    </div>
  );
}

export function LineagePage() {
  const { urn } = useParams({ from: "/shell/lineage/$urn" });
  const decodedUrn = decodeURIComponent(urn);
  const { data: edges, isLoading, error } = useLineage(decodedUrn);
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
        labelStyle: { fill: LIGHT.textMuted, fontSize: 11, fontWeight: 500 },
        labelBgStyle: { fill: LIGHT.panel },
        labelBgPadding: [6, 3],
        labelBgBorderRadius: 6,
        style: { stroke: LIGHT.borderStrong, strokeWidth: 1.5 },
        markerEnd: { type: MarkerType.ArrowClosed, color: LIGHT.borderStrong, width: 18, height: 18 },
      });
    });

    return { nodes: Array.from(nodeMap.values()), flowEdges, edgeInfoById, presentKinds: kindsSeen, propertyCount };
  }, [edges, decodedUrn]);

  const rootKind = urnKind(decodedUrn);
  const backTarget = rootKind === "object-type" ? shortName(decodedUrn) : null;

  if (isLoading) return <LoadingState />;
  if (error)
    return (
      <div style={{ background: LIGHT.bg, margin: -24, padding: 24, minHeight: "100%" }}>
        <p style={{ color: LIGHT.danger, fontSize: 13 }}>{(error as Error).message}</p>
      </div>
    );

  return (
    <div style={{ background: LIGHT.bg, margin: -24, padding: 24, minHeight: "100%" }}>
      <style>{`
        .hl-lineage-node:hover { box-shadow: 0 4px 12px rgba(16, 22, 34, 0.12) !important; transform: translateY(-1px); cursor: pointer; }
        .hl-lineage-edge .react-flow__edge-path { transition: stroke 0.15s ease, stroke-width 0.15s ease; }
        .hl-lineage-edge:hover .react-flow__edge-path { stroke: ${LIGHT.accent} !important; stroke-width: 2 !important; }
        .hl-lineage-edge:hover .react-flow__edge-text { fill: ${LIGHT.accent} !important; }
      `}</style>

      {backTarget && (
        <div style={{ marginBottom: 10 }}>
          <PageBreadcrumbs
            items={[
              { label: "Objects", to: "/objects" },
              { label: backTarget, to: "/objects/$type", params: { type: backTarget } },
              { label: "Lineage" },
            ]}
          />
        </div>
      )}

      <h3 style={{ color: LIGHT.text, fontSize: 20, fontWeight: 600, margin: "0 0 6px" }}>Lineage</h3>
      <div
        style={{
          display: "inline-block",
          fontFamily: "SFMono-Regular, Consolas, Menlo, monospace",
          fontSize: 12,
          color: LIGHT.textMuted,
          background: LIGHT.panel,
          border: `1px solid ${LIGHT.border}`,
          borderRadius: 4,
          padding: "3px 8px",
          marginBottom: 16,
        }}
      >
        {decodedUrn}
      </div>

      <div style={{ display: "flex", gap: 16 }}>
        <div
          style={{
            flex: 1,
            height: 560,
            background: LIGHT.panel,
            border: `1px solid ${LIGHT.border}`,
            borderRadius: 8,
            overflow: "hidden",
          }}
        >
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
            <Background color="#dfe3ea" gap={18} size={1} />
            <Controls />
          </ReactFlow>
        </div>

        <div
          style={{
            width: 300,
            flexShrink: 0,
            background: LIGHT.panel,
            border: `1px solid ${LIGHT.border}`,
            borderRadius: 8,
            padding: 16,
            display: "flex",
            flexDirection: "column",
            gap: 16,
          }}
        >
          <div>
            <div style={{ ...sectionLabel(), marginBottom: 10 }}>Overview</div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Stat value={nodes.length} label={nodes.length === 1 ? "node" : "nodes"} />
              <Stat value={flowEdges.length} label={flowEdges.length === 1 ? "edge" : "edges"} />
              {propertyCount > 0 && <Stat value={propertyCount} label="properties mapped" />}
            </div>
          </div>

          <div>
            <div style={{ ...sectionLabel(), marginBottom: 10 }}>Legend</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {Array.from(presentKinds).map((kind) => {
                const meta = KIND_META[kind];
                return (
                  <div key={kind} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Icon icon={meta.icon} size={12} color={meta.color} />
                    <span style={{ fontSize: 12, color: LIGHT.text }}>{meta.label}</span>
                  </div>
                );
              })}
            </div>
          </div>

          <div style={{ height: 1, background: LIGHT.border }} />

          <div>
            <div style={{ ...sectionLabel(), marginBottom: 10 }}>Details</div>
            {!selection && (
              <p style={{ fontSize: 12, color: LIGHT.textMuted, margin: 0, lineHeight: 1.5 }}>
                Click a node or an edge in the graph to inspect it here.
              </p>
            )}

            {selection?.kind === "node" && (
              <div>
                <div style={{ fontSize: 11, color: LIGHT.textMuted, fontWeight: 600, marginBottom: 6 }}>
                  {KIND_META[urnKind(selection.urn)].label}
                </div>
                <div style={{ fontFamily: "SFMono-Regular, Consolas, Menlo, monospace", fontSize: 11.5, color: LIGHT.text, wordBreak: "break-all" }}>
                  {selection.urn}
                </div>
              </div>
            )}

            {selection?.kind === "edge" && (
              <div>
                <div style={{ fontSize: 11, color: LIGHT.textMuted, fontWeight: 600, marginBottom: 6 }}>
                  {selection.info.relation}
                </div>
                {selection.info.columnMappings.length === 0 ? (
                  <p style={{ fontSize: 12, color: LIGHT.textMuted, margin: 0 }}>Dataset-level edge — no column mapping.</p>
                ) : (
                  <table style={{ width: "100%", fontSize: 11.5, borderCollapse: "collapse" }}>
                    <thead>
                      <tr>
                        <th style={{ textAlign: "left", color: LIGHT.textMuted, fontWeight: 500, paddingBottom: 6 }}>Column</th>
                        <th style={{ textAlign: "left", color: LIGHT.textMuted, fontWeight: 500, paddingBottom: 6 }}>Property</th>
                      </tr>
                    </thead>
                    <tbody>
                      {selection.info.columnMappings.map((m) => (
                        <tr key={m.sourceColumn} style={{ borderTop: `1px solid ${LIGHT.border}` }}>
                          <td style={{ padding: "5px 0", fontFamily: "SFMono-Regular, Consolas, Menlo, monospace", color: LIGHT.text }}>
                            {m.sourceColumn}
                          </td>
                          <td style={{ padding: "5px 0", fontFamily: "SFMono-Regular, Consolas, Menlo, monospace", color: LIGHT.text }}>
                            {m.targetProperty}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        </div>
      </div>

      {(edges ?? []).length === 0 && <p style={{ color: LIGHT.textMuted, marginTop: 12 }}>No lineage edges recorded yet.</p>}
    </div>
  );
}

function Stat({ value, label }: { value: number; label: string }) {
  return (
    <div
      style={{
        background: LIGHT.bg,
        border: `1px solid ${LIGHT.border}`,
        borderRadius: 6,
        padding: "6px 10px",
        display: "flex",
        alignItems: "baseline",
        gap: 5,
      }}
    >
      <span style={{ fontSize: 14, fontWeight: 700, color: LIGHT.text }}>{value}</span>
      <span style={{ fontSize: 11, color: LIGHT.textMuted }}>{label}</span>
    </div>
  );
}
