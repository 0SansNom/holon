import { useMemo } from "react";
import { useParams } from "@tanstack/react-router";
import { H3, Spinner } from "@blueprintjs/core";
import ReactFlow, { Background, Controls, type Edge, type Node } from "reactflow";
import "reactflow/dist/style.css";
import { useLineage } from "../../api/hooks";

function shortName(urn: string): string {
  const parts = urn.split(":");
  return parts[parts.length - 1] ?? urn;
}

export function LineagePage() {
  const { urn } = useParams({ from: "/shell/lineage/$urn" });
  const decodedUrn = decodeURIComponent(urn);
  const { data: edges, isLoading, error } = useLineage(decodedUrn);

  const { nodes, flowEdges } = useMemo(() => {
    const nodeMap = new Map<string, Node>();
    const flowEdges: Edge[] = [];
    let y = 0;

    (edges ?? []).forEach((edge, i) => {
      if (!nodeMap.has(edge.source_urn)) {
        nodeMap.set(edge.source_urn, {
          id: edge.source_urn,
          position: { x: 0, y: y++ * 90 },
          data: { label: shortName(edge.source_urn) },
          style: nodeStyle(edge.source_urn === decodedUrn),
        });
      }
      if (!nodeMap.has(edge.target_urn)) {
        nodeMap.set(edge.target_urn, {
          id: edge.target_urn,
          position: { x: 320, y: y++ * 90 },
          data: { label: shortName(edge.target_urn) },
          style: nodeStyle(edge.target_urn === decodedUrn),
        });
      }
      flowEdges.push({
        id: `e${i}`,
        source: edge.source_urn,
        target: edge.target_urn,
        label: edge.relation,
        animated: true,
      });
    });

    return { nodes: Array.from(nodeMap.values()), flowEdges };
  }, [edges, decodedUrn]);

  if (isLoading) return <Spinner />;
  if (error) return <p style={{ color: "var(--hl-danger)" }}>{(error as Error).message}</p>;

  return (
    <div>
      <H3>Lineage</H3>
      <p className="hl-mono" style={{ color: "var(--hl-text-muted)", marginBottom: 16, fontSize: 12 }}>
        {decodedUrn}
      </p>
      <div className="hl-panel" style={{ height: 500, padding: 0 }}>
        <ReactFlow nodes={nodes} edges={flowEdges} fitView proOptions={{ hideAttribution: true }}>
          <Background color="#262c3a" />
          <Controls />
        </ReactFlow>
      </div>
      {(edges ?? []).length === 0 && <p style={{ color: "var(--hl-text-muted)", marginTop: 12 }}>No lineage edges recorded yet.</p>}
    </div>
  );
}

function nodeStyle(isRoot: boolean) {
  return {
    background: isRoot ? "#1b3a63" : "#171c27",
    color: "#d7dae0",
    border: `1px solid ${isRoot ? "#2d72d2" : "#262c3a"}`,
    borderRadius: 6,
    fontSize: 12,
    padding: 8,
  };
}
