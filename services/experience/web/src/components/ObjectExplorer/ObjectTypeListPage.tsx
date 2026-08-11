import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Card, HTMLSelect, Tag } from "@blueprintjs/core";
import { useObjectSets, useObjectTypes, useObjectTypeGroups } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";
import { CardGrid } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { objectSetBrowsePath, urnShortName } from "./objectExplorerUtils";

export function ObjectTypeListPage() {
  const { data } = useObjectTypes();
  const { data: groups } = useObjectTypeGroups();
  const { data: objectSets = [] } = useObjectSets();
  const [groupFilter, setGroupFilter] = useState("");

  const activeGroup = groups.find((g) => g.name === groupFilter);
  const visibleTypes = activeGroup ? data.filter((ot) => activeGroup.object_types.includes(ot.name)) : data;

  const browseableSets = useMemo(
    () =>
      objectSets
        .filter((os) => os.visibility !== "hidden")
        .sort((a, b) => {
          const rank = (v: string) => (v === "prominent" ? 0 : v === "normal" ? 1 : 2);
          return rank(a.visibility) - rank(b.visibility) || a.name.localeCompare(b.name);
        }),
    [objectSets],
  );

  return (
    <RegistryPage
      title="Objects"
      description={
        <>
          Every ObjectType this ontology defines — instances are only ever reached through it, never a raw table or a
          document dump. Object Sets are filtered, PDP-gated views over those instances.
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
      {browseableSets.length > 0 && (
        <section className="hl-mb-lg">
          <div className="hl-section-title hl-mb-sm">Object Sets</div>
          <CardGrid minWidth={240}>
            {browseableSets.map((os) => {
              const typeName = urnShortName(os.object_type_urn);
              const path = objectSetBrowsePath(typeName, os.name);
              return (
                <Link
                  key={os.urn}
                  to={path.to}
                  params={path.params}
                  search={path.search}
                  className="hl-link-reset"
                >
                  <Card interactive className="hl-h-full">
                    <div className="hl-registry-card-header">
                      <strong className="hl-registry-card-title" title={os.display_name || os.name}>
                        {os.display_name || os.name}
                      </strong>
                      {os.visibility === "prominent" && (
                        <Tag minimal intent="primary">
                          prominent
                        </Tag>
                      )}
                    </div>
                    <div className="hl-tag-row hl-mt-xs">
                      <Tag minimal>{typeName}</Tag>
                      <Tag minimal intent={os.lifecycle_status === "active" ? "success" : "none"}>
                        {os.lifecycle_status}
                      </Tag>
                    </div>
                    {os.description && <p className="hl-card-desc">{os.description}</p>}
                  </Card>
                </Link>
              );
            })}
          </CardGrid>
        </section>
      )}

      <div className="hl-section-title hl-mb-sm">Object types</div>
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
