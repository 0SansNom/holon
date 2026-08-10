import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Card, HTMLSelect } from "@blueprintjs/core";
import { useObjectTypes, useObjectTypeGroups } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";
import { CardGrid } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";

export function ObjectTypeListPage() {
  const { data } = useObjectTypes();
  const { data: groups } = useObjectTypeGroups();
  const [groupFilter, setGroupFilter] = useState("");

  const activeGroup = groups.find((g) => g.name === groupFilter);
  const visibleTypes = activeGroup ? data.filter((ot) => activeGroup.object_types.includes(ot.name)) : data;

  return (
    <RegistryPage
      title="Objects"
      description={
        <>
          Every ObjectType this ontology defines — instances are only ever reached through it, never a raw table or a
          document dump.
        </>
      }
      trailing={
        groups.length > 0 ? (
          <HTMLSelect value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)}>
            <option value="">All groups</option>
            {groups.map((g) => (
              <option key={g.name} value={g.name}>
                {g.name}
              </option>
            ))}
          </HTMLSelect>
        ) : undefined
      }
    >
      {visibleTypes.length === 0 && <p className="hl-text-muted">No ObjectTypes in this group.</p>}
      <CardGrid minWidth={260}>
        {visibleTypes.map((ot) => (
          <Link key={ot.urn} to="/objects/$type" params={{ type: ot.name }} className="hl-link-reset">
            <Card interactive className="hl-h-full">
              <div className="hl-registry-card-header">
                <strong className="hl-registry-card-title" title={ot.name}>
                  {ot.name}
                </strong>
                <ClassificationBadge classification={ot.classification} />
              </div>
              <p className="hl-card-desc">{ot.description}</p>
              <div className="hl-mono hl-text-muted-sm hl-mt-sm">
                v{ot.version} · {Object.keys(ot.property_mapping).length} properties
              </div>
            </Card>
          </Link>
        ))}
      </CardGrid>
    </RegistryPage>
  );
}
