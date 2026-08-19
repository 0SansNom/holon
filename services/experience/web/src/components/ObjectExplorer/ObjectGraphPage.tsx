import { useMemo, useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import { ButtonGroup, Button } from "@blueprintjs/core";
import ReactFlow, { Background, Controls, MarkerType, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { useObjectGraph } from "../../api/hooks";
import { DetailPage } from "../common/PageLayout";
import type { InstanceGraphNode } from "../../api/knowledge";
import { colorForObjectType } from "./objectGraphColors";

function NodeCard({ objectType, label }: { objectType: string; label: string }) {
  const color = colorForObjectType(objectType);
  return (
    <div>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em", color, fontWeight: 600, marginBottom: 4 }}>
        {objectType}
      </div>
      <div style={{ fontSize: 12.5, color: "var(--hl-text)", fontWeight: 500 }}>{label}</div>
    </div>
  );
}

export function ObjectGraphPage() {
  const { type, id } = useParams({ from: "/shell/objects/$type/$id/graph" });
  const navigate = useNavigate();
  const [hops, setHops] = useState(2);
  const { data: graph } = useObjectGraph(type, id, hops);

  const { nodes, flowEdges } = useMemo(() => {
    if (!graph) return { nodes: [] as Node[], flowEdges: [] as Edge[] };
    const columnCounts = new Map<number, number>();
    const nodes: Node[] = graph.nodes.map((n: InstanceGraphNode) => {
      const row = columnCounts.get(n.hop) ?? 0;
      columnCounts.set(n.hop, row + 1);
      return {
        id: n.id,
        position: { x: n.hop * 320, y: row * 90 },
        data: { label: <NodeCard objectType={n.objectType} label={n.label} /> },
        style: nodeStyle(n.id === graph.root, colorForObjectType(n.objectType)),
        className: "hl-graph-node",
      };
    });
    const flowEdges: Edge[] = graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.relation,
      className: "hl-graph-edge",
      labelStyle: { fill: "var(--hl-text-muted)", fontSize: 11, fontWeight: 500 },
      labelBgStyle: { fill: "var(--hl-bg-panel)" },
      labelBgPadding: [6, 3],
      labelBgBorderRadius: 6,
      style: {
        stroke: "var(--hl-border)",
        strokeWidth: 1.5,
        strokeDasharray: e.direction === "toward_many" ? "4 4" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: "var(--hl-border)", width: 18, height: 18 },
    }));
    return { nodes, flowEdges };
  }, [graph]);

  function onNodeClick(_: unknown, node: Node) {
    const [objectType, instanceId] = node.id.split(/:(.+)/);
    if (objectType && instanceId) {
      void navigate({ to: "/objects/$type/$id", params: { type: objectType, id: instanceId } });
    }
  }

  return (
    <DetailPage
      breadcrumbs={[
        { label: "Objects", to: "/objects" },
        { label: type, to: "/objects/$type", params: { type } },
        { label: String(id), to: "/objects/$type/$id", params: { type, id } },
        { label: "Graph" },
      ]}
      title={`${type} / ${id} — related instances`}
      description={
        <>
          Instance-level link analysis over the seeded RelationTypes — click a node to open that object. Dashed
          edges are one-to-many fan-out; solid edges point at a single parent.
        </>
      }
      actions={
        <ButtonGroup>
          <Button active={hops === 2} onClick={() => setHops(2)}>
            2 hops
          </Button>
          <Button active={hops === 3} onClick={() => setHops(3)}>
            3 hops
          </Button>
        </ButtonGroup>
      }
    >
      <style>{`
        .hl-graph-node:hover { box-shadow: 0 4px 12px rgba(16, 22, 34, 0.12) !important; transform: translateY(-1px); cursor: pointer; }
        .hl-graph-edge .react-flow__edge-path { transition: stroke 0.15s ease, stroke-width 0.15s ease; }
        .hl-graph-edge:hover .react-flow__edge-path { stroke: var(--hl-accent) !important; stroke-width: 2 !important; }
        .hl-graph-edge:hover .react-flow__edge-text { fill: var(--hl-accent) !important; }
      `}</style>
      <div className="hl-panel" style={{ height: 500, padding: 0, overflow: "hidden" }}>
        <ReactFlow
          nodes={nodes}
          edges={flowEdges}
          fitView
          fitViewOptions={{ padding: 0.35 }}
          proOptions={{ hideAttribution: true }}
          onNodeClick={onNodeClick}
        >
          <Background color="var(--hl-border)" gap={18} size={1} />
          <Controls />
        </ReactFlow>
      </div>
      {graph?.truncated && (
        <p style={{ color: "var(--hl-warning)", marginTop: 12, fontSize: 12 }}>
          Neighborhood truncated at {graph.nodes.length} nodes — some edges were not expanded further.
        </p>
      )}
      {graph && graph.nodes.length <= 1 && (
        <p className="hl-text-muted hl-mt-md">No related instances found within {hops} hops.</p>
      )}
    </DetailPage>
  );
}

function nodeStyle(isRoot: boolean, accentColor: string | undefined) {
  const accent = accentColor ?? "var(--hl-text-muted)";
  return {
    background: isRoot ? "var(--hl-accent-soft)" : "var(--hl-bg-panel)",
    border: `1.5px solid ${isRoot ? "var(--hl-accent)" : "var(--hl-border)"}`,
    borderRadius: 8,
    fontSize: 12,
    padding: "8px 12px",
    minWidth: 168,
    boxShadow: isRoot ? `0 0 0 3px var(--hl-accent-soft)` : "var(--hl-shadow-sm)",
    borderLeft: `4px solid ${accent}`,
    transition: "box-shadow 0.15s ease, transform 0.15s ease",
  };
}
