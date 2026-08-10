import { useState } from "react";
import { Button, Callout, Tag } from "@blueprintjs/core";
import { useOntologyHealthCheck } from "../../api/hooks";
import { EmptyState } from "../common/ListPrimitives";
import { SkeletonBlock } from "../common/Skeleton";
import { OntologyTabHeader } from "./OntologyTabLayout";
import type { HealthCheckFinding } from "../../api/knowledge";

const KIND_LABELS: Record<HealthCheckFinding["kind"], string> = {
  action_sprawl: "Action Sprawl",
  god_object: "God Object risk",
  misnomer_property: "Possible Misnomer (property)",
  misnomer_type: "Possible Misnomer (ObjectType)",
  dry_duplication: "Possible DRY duplication",
  time_machine: "Possible Time Machine pattern",
};

export function HealthCheckTab() {
  const [triggered, setTriggered] = useState(false);
  const { data, isLoading, isFetching, refetch } = useOntologyHealthCheck(triggered);

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Structural anti-pattern detection, from Foundry's own Ontology design guidance — Action Sprawl, God
            Object, Misnomer, DRY duplication, and Time Machine. Only checks with a real, non-fuzzy signal run; the
            God Object check samples real instance data, so this isn't instant.
          </>
        }
        trailing={
          <Button
            intent="primary"
            icon="diagnosis"
            loading={isLoading || isFetching}
            onClick={() => {
              if (triggered) void refetch();
              else setTriggered(true);
            }}
          >
            Run health check
          </Button>
        }
      />

      {!triggered && <EmptyState>Click "Run health check" to scan the Ontology for anti-patterns.</EmptyState>}

      {triggered && !isLoading && data && data.length === 0 && (
        <Callout intent="success" icon="tick-circle">
          No anti-patterns detected.
        </Callout>
      )}

      {triggered && isLoading && (
        <div className="hl-findings-list" aria-busy aria-label="Running health check">
          {Array.from({ length: 3 }, (_, i) => (
            <SkeletonBlock key={i} width="100%" height={72} />
          ))}
        </div>
      )}

      {triggered && data && data.length > 0 && (
        <div className="hl-findings-list">
          {data.map((finding, i) => (
            <Callout key={i} intent="warning" icon="warning-sign">
              <div className="hl-finding-head">
                <Tag minimal>{KIND_LABELS[finding.kind]}</Tag>
                <strong>{finding.object_type}</strong>
              </div>
              <span className="hl-body-text">{finding.detail}</span>
            </Callout>
          ))}
        </div>
      )}
    </div>
  );
}
