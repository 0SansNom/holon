import { useMemo, useState } from "react";
import { Button, Callout, Checkbox, Tag } from "@blueprintjs/core";
import { Link, useNavigate } from "@tanstack/react-router";
import { useOntologyHealthCheck } from "../../api/hooks";
import { EmptyState } from "../common/ListPrimitives";
import { SkeletonBlock } from "../common/Skeleton";
import { OntologyTabHeader } from "./OntologyTabLayout";
import { isEphemeralTestName, partitionEphemeral } from "./ephemeralResources";
import type { HealthCheckFinding } from "../../api/knowledge";

const KIND_LABELS: Record<HealthCheckFinding["kind"], string> = {
  action_sprawl: "Action Sprawl",
  god_object: "God Object risk",
  misnomer_property: "Possible Misnomer (property)",
  misnomer_type: "Possible Misnomer (ObjectType)",
  dry_duplication: "Possible DRY duplication",
  time_machine: "Possible Time Machine pattern",
  missing_primary_key: "Missing primary key mapping",
  missing_title_key: "Missing title key",
  mn_without_join: "M:N without join storage",
  join_dataset_incomplete: "Incomplete join dataset",
  object_backed_incomplete: "Incomplete object-backed link",
  link_overlays_present: "Link overlay writes present",
  value_type_violation: "Value Type validation failed",
};

const RELATION_FINDING_KINDS = new Set<HealthCheckFinding["kind"]>([
  "mn_without_join",
  "join_dataset_incomplete",
  "object_backed_incomplete",
  "link_overlays_present",
]);

function findingHref(finding: HealthCheckFinding): {
  to: string;
  params?: Record<string, string>;
  search?: Record<string, string>;
  label: string;
} {
  const name = finding.object_type;
  if (name.startsWith("interface:")) {
    return { to: "/ontology", search: { tab: "interfaces" }, label: "Open Interfaces" };
  }
  if (RELATION_FINDING_KINDS.has(finding.kind)) {
    return {
      to: "/ontology/relation-types/$name",
      params: { name },
      label: "Open RelationType",
    };
  }
  if (finding.kind === "action_sprawl") {
    return {
      to: "/ontology/object-types/$name",
      params: { name },
      label: "Open ObjectType",
    };
  }
  return {
    to: "/ontology/object-types/$name",
    params: { name },
    label: "Open ObjectType",
  };
}

export function HealthCheckTab() {
  const navigate = useNavigate();
  const [triggered, setTriggered] = useState(false);
  const [showEphemeral, setShowEphemeral] = useState(false);
  const { data, isLoading, isFetching, refetch } = useOntologyHealthCheck(triggered);

  const { visible, hiddenCount } = useMemo(() => {
    if (!data) return { visible: [] as HealthCheckFinding[], hiddenCount: 0 };
    if (showEphemeral) return { visible: data, hiddenCount: 0 };
    const { kept, hidden } = partitionEphemeral(data, (f) =>
      f.object_type.startsWith("interface:") ? f.object_type.slice("interface:".length) : f.object_type,
    );
    return { visible: kept, hiddenCount: hidden.length };
  }, [data, showEphemeral]);

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Structural anti-pattern detection plus Value Type data checks — Action Sprawl, God Object,
            Misnomer, DRY, Time Machine, and sampled Value Type violations (Foundry OT health). God Object
            and Value Type checks sample real instances, so this isn't instant.
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
        <>
          {(hiddenCount > 0 || showEphemeral) && (
            <div className="hl-flex-row hl-items-center hl-gap-sm hl-mb-sm">
              <Checkbox
                checked={showEphemeral}
                label={
                  showEphemeral
                    ? "Showing ephemeral test leftovers"
                    : `Show ephemeral test leftovers (${hiddenCount} hidden)`
                }
                onChange={(e) => setShowEphemeral(e.currentTarget.checked)}
              />
            </div>
          )}
          {visible.length === 0 ? (
            <Callout intent="success" icon="tick-circle">
              No anti-patterns on durable Ontology resources
              {hiddenCount > 0 ? ` (${hiddenCount} pytest leftovers hidden)` : ""}.
            </Callout>
          ) : (
            <div className="hl-findings-list">
              {visible.map((finding, i) => {
                const href = findingHref(finding);
                return (
                  <Callout
                    key={`${finding.kind}-${finding.object_type}-${i}`}
                    intent={finding.severity === "error" ? "danger" : "warning"}
                    icon={finding.severity === "error" ? "error" : "warning-sign"}
                  >
                    <div className="hl-finding-head">
                      <Tag minimal intent={finding.severity === "error" ? "danger" : "none"}>
                        {KIND_LABELS[finding.kind]}
                      </Tag>
                      <strong className="hl-mono">{finding.object_type}</strong>
                      {isEphemeralTestName(
                        finding.object_type.startsWith("interface:")
                          ? finding.object_type.slice("interface:".length)
                          : finding.object_type,
                      ) ? (
                        <Tag minimal>test</Tag>
                      ) : null}
                      <Button
                        small
                        minimal
                        icon="arrow-right"
                        className="hl-finding-open"
                        onClick={() =>
                          void navigate({
                            to: href.to,
                            params: href.params as never,
                            search: (href.search ?? {}) as never,
                          })
                        }
                      >
                        {href.label}
                      </Button>
                    </div>
                    <span className="hl-body-text">{finding.detail}</span>
                    {RELATION_FINDING_KINDS.has(finding.kind) ? null : finding.object_type.startsWith(
                        "interface:",
                      ) ? null : (
                      <div className="hl-mt-xs">
                        <Link
                          to="/ontology/object-types/$name"
                          params={{ name: finding.object_type }}
                          className="hl-link-accent"
                        >
                          Edit schema
                        </Link>
                      </div>
                    )}
                  </Callout>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
