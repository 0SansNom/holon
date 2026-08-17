import { Button, Card, Tag } from "@blueprintjs/core";
import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import type { ActionDefinition, ObjectType, RelationType } from "../../api/knowledge";
import { urnShortName } from "../ObjectExplorer/objectExplorerUtils";
import type { EditableProperty } from "./propertyEditorUtils";
import { partitionEphemeral } from "./ephemeralResources";

export type ObjectTypeOverviewStep =
  | "identity"
  | "properties"
  | "derived"
  | "advanced"
  | "versions"
  | "datasources";

type RelatedEdge = {
  relationName: string;
  cardinality?: string;
  direction: "out" | "in";
  otherType: string;
  apiName: string;
};

function relatedEdgesForObjectType(objectTypeName: string, relationTypes: RelationType[]): RelatedEdge[] {
  const edges: RelatedEdge[] = [];
  for (const rt of relationTypes) {
    const source = urnShortName(rt.source_object_type_urn);
    const target = urnShortName(rt.target_object_type_urn);
    const localName = rt.name.includes(".") ? rt.name.split(".").slice(1).join(".") : rt.name;
    if (source === objectTypeName) {
      edges.push({
        relationName: rt.name,
        cardinality: rt.cardinality,
        direction: "out",
        otherType: target,
        apiName: (rt.source_api_name || localName).trim() || localName,
      });
    }
    if (target === objectTypeName && source !== objectTypeName) {
      edges.push({
        relationName: rt.name,
        cardinality: rt.cardinality,
        direction: "in",
        otherType: source,
        apiName: (rt.target_api_name || rt.target_property || localName).trim() || localName,
      });
    }
  }
  return edges;
}

function datasetShortName(urn: string | undefined | null): string {
  if (!urn) return "—";
  const parts = urn.split(":");
  return parts[parts.length - 1] || urn;
}

function OverviewSection({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="hl-page-section hl-ot-overview-section">
      <div className="hl-flex-between hl-items-center hl-mb-sm">
        <h4 className="hl-page-section-title" style={{ margin: 0 }}>
          {title}
        </h4>
        {actions}
      </div>
      {children}
    </section>
  );
}

/** Foundry-shaped Object Type Overview (read + jump to edit steps). */
export function ObjectTypeOverview({
  objectType,
  properties,
  derivedCount,
  relationTypes,
  actions,
  groupsContaining = [],
  applicationsUsing = [],
  onNavigateStep,
}: {
  objectType: ObjectType;
  properties: EditableProperty[];
  derivedCount: number;
  relationTypes: RelationType[];
  actions: ActionDefinition[];
  groupsContaining?: string[];
  applicationsUsing?: Array<{ name: string }>;
  onNavigateStep: (step: ObjectTypeOverviewStep) => void;
}) {
  const edges = relatedEdgesForObjectType(objectType.name, relationTypes);
  const relatedActions = actions.filter(
    (a) =>
      a.target_object_type === objectType.name ||
      (a.target_interface != null && (objectType.implements ?? []).includes(a.target_interface)),
  );
  const neighborTypes = [...new Set(edges.map((e) => e.otherType))].sort();
  const mappedProps = properties.filter((p) => p.name.trim() && p.column.trim());
  const appsPartition = partitionEphemeral(applicationsUsing, (app) => app.name);

  return (
    <div className="hl-ot-overview">
      <OverviewSection
        title="Metadata"
        actions={
          <Button minimal small icon="edit" onClick={() => onNavigateStep("identity")}>
            Edit identity
          </Button>
        }
      >
        <div className="hl-tag-row hl-mb-sm">
          <Tag minimal intent="primary">
            {objectType.classification}
          </Tag>
          <Tag minimal>v{objectType.version}</Tag>
          {objectType.lifecycle_status && (
            <Tag
              minimal
              intent={
                objectType.lifecycle_status === "active"
                  ? "success"
                  : objectType.lifecycle_status === "deprecated"
                    ? "warning"
                    : "none"
              }
            >
              {objectType.lifecycle_status}
            </Tag>
          )}
          {objectType.visibility && objectType.visibility !== "normal" && (
            <Tag minimal>{objectType.visibility}</Tag>
          )}
          {objectType.icon && (
            <Tag minimal icon="style">
              {objectType.icon}
            </Tag>
          )}
        </div>
        <dl className="hl-ot-overview-meta">
          <div>
            <dt>Description</dt>
            <dd>{objectType.description?.trim() || "—"}</dd>
          </div>
          <div>
            <dt>Plural display name</dt>
            <dd>{objectType.plural_display_name || "—"}</dd>
          </div>
          <div>
            <dt>Primary key</dt>
            <dd className="hl-mono">{objectType.primary_key || "id"}</dd>
          </div>
          <div>
            <dt>Title key</dt>
            <dd className="hl-mono">{objectType.title_key || "—"}</dd>
          </div>
          <div>
            <dt>Implements</dt>
            <dd>
              {(objectType.implements ?? []).length === 0 ? (
                "—"
              ) : (
                <span className="hl-tag-row">
                  {(objectType.implements ?? []).map((iface) => (
                    <Tag key={iface} minimal icon="link">
                      {iface}
                    </Tag>
                  ))}
                </span>
              )}
            </dd>
          </div>
          <div>
            <dt>URN</dt>
            <dd className="hl-mono hl-text-muted-sm">{objectType.urn}</dd>
          </div>
        </dl>
      </OverviewSection>

      <OverviewSection
        title="Properties"
        actions={
          <Button minimal small icon="edit" onClick={() => onNavigateStep("properties")}>
            Edit properties
          </Button>
        }
      >
        <p className="hl-text-muted-sm hl-mb-sm">
          {mappedProps.length} mapped · {derivedCount} derived
          {properties.length !== mappedProps.length
            ? ` · ${properties.length - mappedProps.length} incomplete in draft`
            : ""}
        </p>
        {mappedProps.length === 0 ? (
          <p className="hl-text-muted">No properties mapped yet.</p>
        ) : (
          <table className="hl-data-table hl-data-table-compact">
            <thead>
              <tr>
                <th>API name</th>
                <th>Backing column</th>
                <th>Visibility</th>
                <th>Type</th>
              </tr>
            </thead>
            <tbody>
              {mappedProps.slice(0, 12).map((p) => (
                <tr key={p.name}>
                  <td className="hl-mono">{p.name}</td>
                  <td className="hl-mono">{p.column}</td>
                  <td>{p.visibility || "normal"}</td>
                  <td className="hl-mono">
                    {p.typeKind === "shared_property_type"
                      ? p.sharedPropertyType || "SPT"
                      : p.typeKind === "struct"
                        ? "struct"
                        : p.typeKind === "array"
                          ? "array"
                          : p.valueType || "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {mappedProps.length > 12 && (
          <Button minimal small className="hl-mt-sm" onClick={() => onNavigateStep("properties")}>
            View all {mappedProps.length} properties
          </Button>
        )}
      </OverviewSection>

      <OverviewSection title="Action types">
        {relatedActions.length === 0 ? (
          <p className="hl-text-muted">No Action Types target this ObjectType (or its interfaces).</p>
        ) : (
          <div className="hl-ot-overview-card-grid">
            {relatedActions.map((action) => (
              <Card key={action.name} className="hl-ot-overview-mini-card">
                <Link
                  to="/ontology/action-types/$name"
                  params={{ name: action.name }}
                  className="hl-link-reset"
                >
                  <strong className="hl-mono" title={action.name}>
                    {action.name.includes(".") ? action.name.split(".").slice(1).join(".") : action.name}
                  </strong>
                </Link>
                <div className="hl-tag-row hl-mt-xs">
                  <Tag minimal intent={action.risk_level === "high" ? "danger" : "none"}>
                    {action.risk_level}
                  </Tag>
                  {action.target_interface && (
                    <Tag minimal icon="link">
                      {action.target_interface}
                    </Tag>
                  )}
                </div>
                {action.description && <p className="hl-card-desc">{action.description}</p>}
              </Card>
            ))}
          </div>
        )}
        <p className="hl-text-muted-sm hl-mt-sm">
          Manage Action Types from the{" "}
          <Link to="/ontology" search={{ tab: "action-types" }} className="hl-link-accent">
            Ontology → Action Types
          </Link>{" "}
          tab.
        </p>
      </OverviewSection>

      <OverviewSection title="RelationTypes">
        {edges.length === 0 ? (
          <p className="hl-text-muted">No RelationTypes touch this ObjectType.</p>
        ) : (
          <>
            <div className="hl-ot-overview-link-graph" aria-label="RelationType neighborhood">
              <div className="hl-ot-overview-link-hub">
                <Tag large intent="primary">
                  {objectType.name}
                </Tag>
              </div>
              <ul className="hl-ot-overview-link-edges">
                {edges.map((edge) => (
                  <li key={`${edge.relationName}-${edge.direction}-${edge.otherType}`}>
                    <span className="hl-mono hl-text-muted-sm">
                      {edge.direction === "out" ? "→" : "←"} {edge.apiName}
                      {edge.cardinality ? ` (${edge.cardinality})` : ""}
                    </span>
                    <Link
                      to="/objects/$type"
                      params={{ type: edge.otherType }}
                      className="hl-link-accent"
                    >
                      {edge.otherType}
                    </Link>
                    <Link
                      to="/ontology/relation-types/$name"
                      params={{ name: edge.relationName }}
                      className="hl-mono hl-link-accent"
                    >
                      {edge.relationName}
                    </Link>
                  </li>
                ))}
              </ul>
            </div>
            {neighborTypes.length > 0 && (
              <p className="hl-text-muted-sm hl-mt-sm">
                Neighbor ObjectTypes:{" "}
                {neighborTypes.map((t, i) => (
                  <span key={t}>
                    {i > 0 ? ", " : ""}
                    <Link to="/objects/$type" params={{ type: t }} className="hl-link-accent">
                      {t}
                    </Link>
                  </span>
                ))}
              </p>
            )}
          </>
        )}
        <p className="hl-text-muted-sm hl-mt-sm">
          Manage RelationTypes from the{" "}
          <Link to="/ontology" search={{ tab: "relation-types" }} className="hl-link-accent">
            Ontology → RelationTypes
          </Link>{" "}
          tab.
        </p>
      </OverviewSection>

      <OverviewSection
        title="Data"
        actions={
          <Button minimal small icon="database" onClick={() => onNavigateStep("datasources")}>
            Datasources
          </Button>
        }
      >
        <dl className="hl-ot-overview-meta">
          <div>
            <dt>Source dataset</dt>
            <dd className="hl-mono" title={objectType.source_dataset_urn}>
              {datasetShortName(objectType.source_dataset_urn)}
            </dd>
          </div>
          <div>
            <dt>Backing columns</dt>
            <dd>
              {mappedProps.length === 0
                ? "—"
                : mappedProps
                    .slice(0, 8)
                    .map((p) => p.column)
                    .join(", ") + (mappedProps.length > 8 ? ` (+${mappedProps.length - 8})` : "")}
            </dd>
          </div>
        </dl>
        <div className="hl-flex-row hl-gap-sm hl-mt-sm" style={{ flexWrap: "wrap" }}>
          <Link to="/objects/$type" params={{ type: objectType.name }} className="hl-link-accent">
            Browse objects in Object Explorer
          </Link>
          <span className="hl-text-muted-sm">·</span>
          <Link to="/catalog" className="hl-link-accent">
            Open Catalog
          </Link>
          <span className="hl-text-muted-sm">·</span>
          <button type="button" className="hl-link-accent" onClick={() => onNavigateStep("datasources")}>
            View column mapping
          </button>
        </div>
      </OverviewSection>

      <OverviewSection title="Dependents & usage">
        <p className="hl-text-muted-sm hl-mb-sm">
          Resources that reference this ObjectType — RelationTypes, Action Types, Applications, and groups.
        </p>
        <div className="hl-ot-overview-deps-grid">
          <div>
            <h5 className="hl-text-muted-sm">RelationTypes ({edges.length})</h5>
            {edges.length === 0 ? (
              <p className="hl-text-muted">None</p>
            ) : (
              <ul className="hl-ot-overview-deps">
                {edges.slice(0, 8).map((edge) => (
                  <li key={`${edge.relationName}-${edge.direction}`}>
                    <Link
                      to="/ontology/relation-types/$name"
                      params={{ name: edge.relationName }}
                      className="hl-link-accent hl-mono"
                    >
                      {edge.relationName}
                    </Link>
                    <span className="hl-text-muted-sm">
                      {" "}
                      {edge.direction === "out" ? "→" : "←"} {edge.otherType}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h5 className="hl-text-muted-sm">Action Types ({relatedActions.length})</h5>
            {relatedActions.length === 0 ? (
              <p className="hl-text-muted">None</p>
            ) : (
              <ul className="hl-ot-overview-deps">
                {relatedActions.slice(0, 8).map((action) => (
                  <li key={action.name}>
                    <Link
                      to="/ontology/action-types/$name"
                      params={{ name: action.name }}
                      className="hl-link-accent hl-mono"
                    >
                      {action.name.includes(".") ? action.name.split(".").slice(1).join(".") : action.name}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h5 className="hl-text-muted-sm">
              Applications ({appsPartition.kept.length}
              {appsPartition.hidden.length > 0 ? ` · ${appsPartition.hidden.length} test hidden` : ""})
            </h5>
            {appsPartition.kept.length === 0 ? (
              <p className="hl-text-muted">
                {applicationsUsing.length === 0
                  ? "None declared in dependencies"
                  : "None outside pytest leftovers"}
              </p>
            ) : (
              <ul className="hl-ot-overview-deps">
                {appsPartition.kept.map((app) => (
                  <li key={app.name}>
                    <Link
                      to="/applications/$name"
                      params={{ name: app.name }}
                      className="hl-link-accent"
                    >
                      {app.name}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
          <div>
            <h5 className="hl-text-muted-sm">Groups ({groupsContaining.length})</h5>
            {groupsContaining.length === 0 ? (
              <p className="hl-text-muted">None</p>
            ) : (
              <ul className="hl-ot-overview-deps">
                {groupsContaining.map((groupName) => (
                  <li key={groupName}>
                    <Link to="/ontology" search={{ tab: "object-type-groups" }} className="hl-link-accent">
                      {groupName}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
        <Link to="/lineage/$urn" params={{ urn: objectType.urn }} className="hl-link-accent">
          Open lineage graph
        </Link>
      </OverviewSection>
    </div>
  );
}
