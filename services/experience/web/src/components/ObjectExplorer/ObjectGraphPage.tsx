import { useMemo, useState } from "react";
import { useNavigate, useParams } from "@tanstack/react-router";
import { ButtonGroup, Button, H3, Spinner } from "@blueprintjs/core";
import ReactFlow, { Background, Controls, MarkerType, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { useObjectGraph } from "../../api/hooks";
import { PageBreadcrumbs } from "../common/PageBreadcrumbs";
import type { InstanceGraphNode } from "../../api/knowledge";

// Categorical accent, secondary to the node's own label (which already
// states the friendly instance name in plain text) — never the sole
// channel of identity, which is why each node card also carries a small
// text badge naming its ObjectType. Per the dataviz skill's reference
// palette; beyond its first 3 slots, all-pairs CVD separation isn't
// guaranteed (the palette's own documented limit, not a new gap
// introduced here), acceptable specifically because color is redundant
// with the always-visible type badge, not load-bearing on its own.
const TYPE_COLORS: Record<string, string> = {
  Customer: "#2d63c8",
  Order: "#b8551f",
  SupportTicket: "#0e8a5f",
  ProductReview: "#8f6a1f",
  Supplier: "#a8386b",
  InventoryLevel: "#3a8a4a",
};

function NodeCard({ objectType, label }: { objectType: string; label: string }) {
  const color = TYPE_COLORS[objectType] ?? "#5f6b7a";
  return (
    <div>
      <div style={{ fontSize: 10, textTransform: "uppercase", letterSpacing: "0.04em", color, fontWeight: 600, marginBottom: 4 }}>
        {objectType}
      </div>
      <div style={{ fontSize: 12.5, color: "#1c2127", fontWeight: 500 }}>{label}</div>
    </div>
  );
}

export function ObjectGraphPage() {
  const { type, id } = useParams({ from: "/shell/objects/$type/$id/graph" });
  const navigate = useNavigate();
  const [hops, setHops] = useState(2);
  const { data: graph, isLoading, error } = useObjectGraph(type, id, hops);

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
        style: nodeStyle(n.id === graph.root, TYPE_COLORS[n.objectType]),
        className: "hl-graph-node",
      };
    });
    const flowEdges: Edge[] = graph.edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      label: e.relation,
      className: "hl-graph-edge",
      labelStyle: { fill: "#5f6b7a", fontSize: 11, fontWeight: 500 },
      labelBgStyle: { fill: "#ffffff" },
      labelBgPadding: [6, 3],
      labelBgBorderRadius: 6,
      style: {
        stroke: "#c7cdd8",
        strokeWidth: 1.5,
        strokeDasharray: e.direction === "toward_many" ? "4 4" : undefined,
      },
      markerEnd: { type: MarkerType.ArrowClosed, color: "#c7cdd8", width: 18, height: 18 },
    }));
    return { nodes, flowEdges };
  }, [graph]);

  function onNodeClick(_: unknown, node: Node) {
    const [objectType, instanceId] = node.id.split(/:(.+)/);
    if (objectType && instanceId) {
      void navigate({ to: "/objects/$type/$id", params: { type: objectType, id: instanceId } });
    }
  }

  if (isLoading) return <Spinner />;
  if (error) return <p style={{ color: "var(--hl-danger)" }}>{(error as Error).message}</p>;

  return (
    <div>
      <PageBreadcrumbs
        items={[
          { label: "Objects", to: "/objects" },
          { label: type, to: "/objects/$type", params: { type } },
          { label: String(id), to: "/objects/$type/$id", params: { type, id } },
          { label: "Graph" },
        ]}
      />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
        <H3 style={{ margin: 0 }}>
          {type} / {id} — related instances
        </H3>
        <ButtonGroup>
          <Button active={hops === 2} onClick={() => setHops(2)}>
            2 hops
          </Button>
          <Button active={hops === 3} onClick={() => setHops(3)}>
            3 hops
          </Button>
        </ButtonGroup>
      </div>
      <p style={{ color: "var(--hl-text-muted)", marginBottom: 16, fontSize: 13 }}>
        Instance-level link analysis over the seeded RelationTypes — click a node to open that object. Dashed
        edges are one-to-many fan-out; solid edges point at a single parent.
      </p>
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
          <Background color="#dfe3ea" gap={18} size={1} />
          <Controls />
        </ReactFlow>
      </div>
      {graph?.truncated && (
        <p style={{ color: "var(--hl-warning)", marginTop: 12, fontSize: 12 }}>
          Neighborhood truncated at {graph.nodes.length} nodes — some edges were not expanded further.
        </p>
      )}
      {graph && graph.nodes.length <= 1 && (
        <p style={{ color: "var(--hl-text-muted)", marginTop: 12 }}>No related instances found within {hops} hops.</p>
      )}
    </div>
  );
}

function nodeStyle(isRoot: boolean, accentColor: string | undefined) {
  const accent = accentColor ?? "#5f6b7a";
  return {
    background: isRoot ? "#eaf1fd" : "#ffffff",
    border: `1.5px solid ${isRoot ? "#2d63c8" : "#e1e4ea"}`,
    borderRadius: 8,
    fontSize: 12,
    padding: "8px 12px",
    minWidth: 168,
    boxShadow: isRoot ? "0 0 0 3px #eaf1fd" : "0 1px 2px rgba(16, 22, 34, 0.06)",
    borderLeft: `4px solid ${accent}`,
    transition: "box-shadow 0.15s ease, transform 0.15s ease",
  };
}
