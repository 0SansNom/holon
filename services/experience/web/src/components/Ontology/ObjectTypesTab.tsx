import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Button, Card, Checkbox, HTMLSelect, Tag } from "@blueprintjs/core";
import { useObjectTypes, useObjectTypeGroups } from "../../api/hooks";
import type { ObjectType } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { ResourceActionsMenu, ResourceTagBadges } from "../common/ResourceActionsMenu";
import { BranchesDialog } from "./BranchesDialog";
import { isEphemeralTestName } from "./ephemeralResources";
import { OntologyTabHeader } from "./OntologyTabLayout";

export function ObjectTypesTab() {
  const navigate = useNavigate();
  const { data } = useObjectTypes();
  const { data: groups } = useObjectTypeGroups();
  const [branching, setBranching] = useState<ObjectType | null>(null);
  const [groupFilter, setGroupFilter] = useState<string>("");
  const [showEphemeral, setShowEphemeral] = useState(false);

  const activeGroup = groups.find((g) => g.name === groupFilter);
  const scoped = activeGroup ? data.filter((ot) => activeGroup.object_types.includes(ot.name)) : data;
  const ephemeralCount = scoped.filter((ot) => isEphemeralTestName(ot.name)).length;
  const visibleTypes = showEphemeral ? scoped : scoped.filter((ot) => !isEphemeralTestName(ot.name));

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            ObjectTypes are created self-serve from a synced dataset (see the Sources page) — open an ObjectType to
            edit its draft schema (identity, properties, derived), propose a version, then publish it to go live.
          </>
        }
        trailing={
          <div className="hl-flex-row hl-items-center hl-gap-sm">
            {ephemeralCount > 0 ? (
              <Checkbox
                checked={showEphemeral}
                label={`Show test leftovers (${ephemeralCount})`}
                onChange={(e) => setShowEphemeral(e.currentTarget.checked)}
                style={{ marginBottom: 0 }}
              />
            ) : null}
            {groups.length > 0 ? (
              <HTMLSelect value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)}>
                <option value="">All groups</option>
                {groups.map((g) => (
                  <option key={g.name} value={g.name}>
                    {g.name}
                  </option>
                ))}
              </HTMLSelect>
            ) : null}
          </div>
        }
      />

      <CardGrid minWidth={260}>
        {visibleTypes.map((ot) => (
          <Card key={ot.urn}>
            <div className="hl-registry-card-header">
              <strong className="hl-registry-card-title" title={ot.name}>
                {ot.name}
              </strong>
              <ResourceActionsMenu urn={ot.urn} />
            </div>
            <div className="hl-tag-row hl-mt-xs">
              <Tag minimal>{ot.classification}</Tag>
              <Tag minimal>v{ot.version}</Tag>
              {ot.lifecycle_status && (
                <Tag
                  minimal
                  intent={
                    ot.lifecycle_status === "active"
                      ? "success"
                      : ot.lifecycle_status === "deprecated"
                        ? "warning"
                        : "none"
                  }
                >
                  {ot.lifecycle_status}
                </Tag>
              )}
              {ot.visibility && ot.visibility !== "normal" && <Tag minimal>{ot.visibility}</Tag>}
              {ot.title_key && <Tag minimal>title:{ot.title_key}</Tag>}
              {(ot.implements ?? []).map((i) => (
                <Tag key={i} minimal icon="link">
                  {i}
                </Tag>
              ))}
              <ResourceTagBadges urn={ot.urn} />
            </div>
            {ot.description && <p className="hl-card-desc">{ot.description}</p>}
            <div className="hl-card-actions">
              <Button
                small
                minimal
                icon="document-open"
                onClick={() =>
                  void navigate({ to: "/ontology/object-types/$name", params: { name: ot.name } })
                }
              >
                Open
              </Button>
              <Button small minimal icon="git-branch" onClick={() => setBranching(ot)}>
                Branches
              </Button>
            </div>
          </Card>
        ))}
        {visibleTypes.length === 0 && (
          <EmptyState>{activeGroup ? "No ObjectTypes in this group." : "No ObjectTypes yet."}</EmptyState>
        )}
      </CardGrid>

      {branching && (
        <BranchesDialog
          kind="object_type"
          resourceName={branching.name}
          currentDefinition={{
            property_mapping: branching.property_mapping,
            description: branching.description,
            implements: branching.implements ?? [],
            derived_properties: branching.derived_properties ?? {},
            project_urn: branching.project_urn ?? null,
            markings: branching.markings ?? [],
            property_formats: branching.property_formats,
            conditional_formats: branching.conditional_formats ?? {},
            property_types: branching.property_types ?? {},
            link_constraint_bindings: branching.link_constraint_bindings ?? {},
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
