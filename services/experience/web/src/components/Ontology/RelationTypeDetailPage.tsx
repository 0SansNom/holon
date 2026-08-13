import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { Button, FormGroup, HTMLSelect, InputGroup, Tab, Tabs, Tag, type TabId } from "@blueprintjs/core";
import {
  useObjectTypes,
  useProjects,
  useRelationTypes,
  useUpdateRelationType,
} from "../../api/hooks";
import { DetailPage, PageSection } from "../common/PageLayout";
import { EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { BranchesDialog } from "./BranchesDialog";
import { RelationPermissionsPanel, RelationWritebackWarning, CARDINALITIES, STORAGE_KINDS, VISIBILITIES } from "./RelationTypesTab";
import { urnShortName } from "../ObjectExplorer/objectExplorerUtils";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useOntologyDiscoverStore } from "../../store/ontologyDiscover";
import { REGISTRY_LIFECYCLE_STATUSES } from "./lifecycleUtils";
import { parseTypeClassesInput } from "./typeClassUtils";

type DetailTab = "overview" | "datasources";

const LIFECYCLE_STATUSES = REGISTRY_LIFECYCLE_STATUSES;

export function RelationTypeDetailPage() {
  const { name: rawName } = useParams({ from: "/shell/ontology/relation-types/$name" });
  const name = decodeURIComponent(rawName);
  const navigate = useNavigate();
  const { data: relationTypes = [] } = useRelationTypes();
  const { data: objectTypes = [] } = useObjectTypes();
  const { data: projects = [] } = useProjects();
  const updateRelationType = useUpdateRelationType();
  const relationType = useMemo(() => relationTypes.find((r) => r.name === name), [relationTypes, name]);
  const [tab, setTab] = useState<DetailTab>("overview");
  const [branching, setBranching] = useState(false);
  const [editing, setEditing] = useState(false);

  const recordVisit = useOntologyDiscoverStore((s) => s.recordVisit);
  const isFavorite = useOntologyDiscoverStore((s) => s.isFavorite("relation_type", name));
  const toggleFavorite = useOntologyDiscoverStore((s) => s.toggleFavorite);

  const [editTargetProperty, setEditTargetProperty] = useState("");
  const [editCardinality, setEditCardinality] = useState("many_to_one");
  const [editStorageKind, setEditStorageKind] = useState("foreign_key");
  const [editJoinDatasetUrn, setEditJoinDatasetUrn] = useState("");
  const [editJoinSourceColumn, setEditJoinSourceColumn] = useState("");
  const [editJoinTargetColumn, setEditJoinTargetColumn] = useState("");
  const [editMidObjectType, setEditMidObjectType] = useState("");
  const [editMidSourceProperty, setEditMidSourceProperty] = useState("");
  const [editMidTargetProperty, setEditMidTargetProperty] = useState("");
  const [editSourceDisplayName, setEditSourceDisplayName] = useState("");
  const [editSourcePluralDisplayName, setEditSourcePluralDisplayName] = useState("");
  const [editSourceApiName, setEditSourceApiName] = useState("");
  const [editSourceVisibility, setEditSourceVisibility] = useState("normal");
  const [editTargetDisplayName, setEditTargetDisplayName] = useState("");
  const [editTargetPluralDisplayName, setEditTargetPluralDisplayName] = useState("");
  const [editTargetApiName, setEditTargetApiName] = useState("");
  const [editTargetVisibility, setEditTargetVisibility] = useState("normal");
  const [editLifecycleStatus, setEditLifecycleStatus] = useState("experimental");
  const [editDeprecationReason, setEditDeprecationReason] = useState("");
  const [editDeprecationDeadline, setEditDeprecationDeadline] = useState("");
  const [editReplacementUrn, setEditReplacementUrn] = useState("");
  const [editTypeClasses, setEditTypeClasses] = useState("");
  const [editProjectUrn, setEditProjectUrn] = useState("");

  useEffect(() => {
    if (relationType) recordVisit("relation_type", relationType.name);
  }, [relationType, recordVisit]);

  function openEdit() {
    if (!relationType) return;
    setEditTargetProperty(relationType.target_property ?? "");
    setEditCardinality(relationType.cardinality);
    setEditStorageKind(relationType.storage_kind ?? "foreign_key");
    setEditJoinDatasetUrn(relationType.join_dataset_urn ?? "");
    setEditJoinSourceColumn(relationType.join_source_column ?? "");
    setEditJoinTargetColumn(relationType.join_target_column ?? "");
    setEditMidObjectType(relationType.mid_object_type_urn ? urnShortName(relationType.mid_object_type_urn) : "");
    setEditMidSourceProperty(relationType.mid_source_property ?? "");
    setEditMidTargetProperty(relationType.mid_target_property ?? "");
    setEditSourceDisplayName(relationType.source_display_name ?? "");
    setEditSourcePluralDisplayName(relationType.source_plural_display_name ?? "");
    setEditSourceApiName(relationType.source_api_name || relationType.name.split(".").at(-1) || "");
    setEditSourceVisibility(relationType.source_visibility ?? "normal");
    setEditTargetDisplayName(relationType.target_display_name ?? "");
    setEditTargetPluralDisplayName(relationType.target_plural_display_name ?? "");
    setEditTargetApiName(relationType.target_api_name || relationType.target_property || "");
    setEditTargetVisibility(relationType.target_visibility ?? "normal");
    setEditLifecycleStatus(relationType.lifecycle_status ?? "experimental");
    setEditDeprecationReason(relationType.deprecation_reason ?? "");
    setEditDeprecationDeadline((relationType.deprecation_deadline ?? "").toString().slice(0, 10));
    setEditReplacementUrn(relationType.replacement_urn ?? "");
    setEditTypeClasses((relationType.type_classes ?? []).join(", "));
    setEditProjectUrn(relationType.project_urn ?? "");
    setEditing(true);
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!relationType) return;
    const previousProject = relationType.project_urn ?? "";
    await updateRelationType.mutateAsync({
      name: relationType.name,
      body: {
        target_property: editTargetProperty,
        cardinality: editCardinality,
        storage_kind: editStorageKind,
        join_dataset_urn: editJoinDatasetUrn || undefined,
        join_source_column: editJoinSourceColumn || undefined,
        join_target_column: editJoinTargetColumn || undefined,
        mid_object_type: editMidObjectType || undefined,
        mid_source_property: editMidSourceProperty || undefined,
        mid_target_property: editMidTargetProperty || undefined,
        source_display_name: editSourceDisplayName,
        source_plural_display_name: editSourcePluralDisplayName,
        source_api_name: editSourceApiName,
        source_visibility: editSourceVisibility,
        target_display_name: editTargetDisplayName,
        target_plural_display_name: editTargetPluralDisplayName,
        target_api_name: editTargetApiName,
        target_visibility: editTargetVisibility,
        lifecycle_status: editLifecycleStatus,
        deprecation_reason: editLifecycleStatus === "deprecated" ? editDeprecationReason : undefined,
        deprecation_deadline: editLifecycleStatus === "deprecated" ? editDeprecationDeadline || undefined : undefined,
        replacement_urn: editLifecycleStatus === "deprecated" ? editReplacementUrn || undefined : undefined,
        type_classes: parseTypeClassesInput(editTypeClasses),
        project_urn: editProjectUrn || undefined,
        clear_project_urn: !editProjectUrn && !!previousProject,
      },
    });
    setEditing(false);
  }, { successMessage: `"${name}" saved` });

  if (!relationType) {
    return (
      <DetailPage
        breadcrumbs={[
          { label: "Ontology", to: "/ontology", search: { tab: "relation-types" } },
          { label: name },
        ]}
        title={name}
      >
        <EmptyState>RelationType not found.</EmptyState>
      </DetailPage>
    );
  }

  const sourceType = urnShortName(relationType.source_object_type_urn);
  const targetType = urnShortName(relationType.target_object_type_urn);
  const storage = relationType.storage_kind ?? "foreign_key";
  const otOptions = objectTypes.map((ot) => ot.name);

  return (
    <DetailPage
      breadcrumbs={[
        { label: "Ontology", to: "/ontology", search: { tab: "relation-types" } },
        { label: relationType.name },
      ]}
      title={relationType.name}
      description={
        <span className="hl-tag-row">
          <Tag minimal intent="primary">
            RelationType
          </Tag>
          <Tag minimal>{relationType.cardinality}</Tag>
          <Tag minimal>{storage}</Tag>
          <Tag minimal>{relationType.lifecycle_status ?? "experimental"}</Tag>
        </span>
      }
      actions={
        <div className="hl-flex-row hl-gap-sm">
          <Button
            icon={isFavorite ? "star" : "star-empty"}
            intent={isFavorite ? "warning" : "none"}
            onClick={() => toggleFavorite("relation_type", relationType.name)}
          >
            {isFavorite ? "Favorited" : "Favorite"}
          </Button>
          <Button icon="edit" onClick={openEdit}>
            Edit
          </Button>
          <Button icon="git-branch" onClick={() => setBranching(true)}>
            Branches
          </Button>
        </div>
      }
    >
      <Tabs
        id="relation-type-detail"
        selectedTabId={tab}
        onChange={(id: TabId) => setTab(String(id) as DetailTab)}
        renderActiveTabPanelOnly
        className="hl-ot-draft-tabs"
      >
        <Tab
          id="overview"
          title="Overview"
          panel={
            <PageSection title="Overview">
              <dl className="hl-ot-overview-meta">
                <div>
                  <dt>Source ObjectType</dt>
                  <dd>
                    <Link to="/ontology/object-types/$name" params={{ name: sourceType }} className="hl-link-accent">
                      {sourceType}
                    </Link>
                    <span className="hl-mono hl-text-muted-sm">
                      {" "}
                      · {relationType.source_api_name || relationType.source_property}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>Target ObjectType</dt>
                  <dd>
                    <Link to="/ontology/object-types/$name" params={{ name: targetType }} className="hl-link-accent">
                      {targetType}
                    </Link>
                    <span className="hl-mono hl-text-muted-sm">
                      {" "}
                      · {relationType.target_api_name || relationType.target_property}
                    </span>
                  </dd>
                </div>
                <div>
                  <dt>Cardinality</dt>
                  <dd className="hl-mono">{relationType.cardinality}</dd>
                </div>
                <div>
                  <dt>Source side</dt>
                  <dd>
                    {relationType.source_display_name || "—"}
                    {relationType.source_visibility ? ` · ${relationType.source_visibility}` : ""}
                  </dd>
                </div>
                <div>
                  <dt>Target side</dt>
                  <dd>
                    {relationType.target_display_name || "—"}
                    {relationType.target_visibility ? ` · ${relationType.target_visibility}` : ""}
                  </dd>
                </div>
                <div>
                  <dt>URN</dt>
                  <dd className="hl-mono hl-text-muted-sm">{relationType.urn}</dd>
                </div>
              </dl>
              <p className="hl-text-muted-sm hl-mt-md">
                Storage and writeback details are on the{" "}
                <button type="button" className="hl-link-accent" onClick={() => setTab("datasources")}>
                  Datasources
                </button>{" "}
                tab.
              </p>
            </PageSection>
          }
        />
        <Tab
          id="datasources"
          title="Datasources"
          panel={
            <PageSection title="Datasources">
              <RelationWritebackWarning name={relationType.name} />
              <dl className="hl-ot-overview-meta hl-mb-md">
                <div>
                  <dt>Storage kind</dt>
                  <dd className="hl-mono">{storage}</dd>
                </div>
                {storage === "foreign_key" && (
                  <>
                    <div>
                      <dt>Source property</dt>
                      <dd className="hl-mono">{relationType.source_property}</dd>
                    </div>
                    <div>
                      <dt>Target property</dt>
                      <dd className="hl-mono">{relationType.target_property}</dd>
                    </div>
                  </>
                )}
                {storage === "join_dataset" && (
                  <>
                    <div>
                      <dt>Join dataset</dt>
                      <dd className="hl-mono">{relationType.join_dataset_urn || "—"}</dd>
                    </div>
                    <div>
                      <dt>Join columns</dt>
                      <dd className="hl-mono">
                        {relationType.join_source_column || "—"} → {relationType.join_target_column || "—"}
                      </dd>
                    </div>
                  </>
                )}
                {storage === "object_backed" && (
                  <>
                    <div>
                      <dt>Mid ObjectType</dt>
                      <dd className="hl-mono">
                        {relationType.mid_object_type_urn
                          ? urnShortName(relationType.mid_object_type_urn)
                          : "—"}
                      </dd>
                    </div>
                    <div>
                      <dt>Mid properties</dt>
                      <dd className="hl-mono">
                        {relationType.mid_source_property || "—"} / {relationType.mid_target_property || "—"}
                      </dd>
                    </div>
                  </>
                )}
              </dl>
              <RelationPermissionsPanel name={relationType.name} />
            </PageSection>
          }
        />
      </Tabs>

      {branching && (
        <BranchesDialog
          kind="relation_type"
          resourceName={relationType.name}
          currentDefinition={{
            source_object_type: sourceType,
            target_object_type: targetType,
            source_object_type_urn: relationType.source_object_type_urn,
            target_object_type_urn: relationType.target_object_type_urn,
            source_property: relationType.source_property,
            target_property: relationType.target_property,
            cardinality: relationType.cardinality,
            storage_kind: relationType.storage_kind ?? "foreign_key",
            join_dataset_urn: relationType.join_dataset_urn ?? null,
            join_source_column: relationType.join_source_column ?? null,
            join_target_column: relationType.join_target_column ?? null,
            mid_object_type_urn: relationType.mid_object_type_urn ?? null,
            mid_object_type: relationType.mid_object_type_urn
              ? urnShortName(relationType.mid_object_type_urn)
              : null,
            mid_source_property: relationType.mid_source_property ?? null,
            mid_target_property: relationType.mid_target_property ?? null,
            source_display_name: relationType.source_display_name ?? "",
            source_plural_display_name: relationType.source_plural_display_name ?? "",
            source_api_name: relationType.source_api_name ?? "",
            source_visibility: relationType.source_visibility ?? "normal",
            target_display_name: relationType.target_display_name ?? "",
            target_plural_display_name: relationType.target_plural_display_name ?? "",
            target_api_name: relationType.target_api_name ?? "",
            target_visibility: relationType.target_visibility ?? "normal",
            lifecycle_status: relationType.lifecycle_status ?? "experimental",
            type_classes: relationType.type_classes ?? [],
            project_urn: relationType.project_urn ?? null,
          }}
          onClose={() => setBranching(false)}
        />
      )}

      <RegistryDialog
        isOpen={editing}
        title={`Edit ${relationType.name}`}
        onClose={() => setEditing(false)}
        onSubmit={() => submitEdit(undefined)}
        submitLabel="Save"
        isPending={editPending}
        error={editError}
      >
        <RelationWritebackWarning name={relationType.name} />
        <FormGroup label="Target property">
          <InputGroup value={editTargetProperty} onChange={(e) => setEditTargetProperty(e.target.value)} />
        </FormGroup>
        <FormGroup label="Cardinality">
          <HTMLSelect fill value={editCardinality} onChange={(e) => setEditCardinality(e.target.value)}>
            {CARDINALITIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Storage kind">
          <HTMLSelect fill value={editStorageKind} onChange={(e) => setEditStorageKind(e.target.value)}>
            {STORAGE_KINDS.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        {editStorageKind === "join_dataset" && (
          <>
            <FormGroup label="Join dataset URN">
              <InputGroup value={editJoinDatasetUrn} onChange={(e) => setEditJoinDatasetUrn(e.target.value)} />
            </FormGroup>
            <FormGroup label="Join source column">
              <InputGroup value={editJoinSourceColumn} onChange={(e) => setEditJoinSourceColumn(e.target.value)} />
            </FormGroup>
            <FormGroup label="Join target column">
              <InputGroup value={editJoinTargetColumn} onChange={(e) => setEditJoinTargetColumn(e.target.value)} />
            </FormGroup>
          </>
        )}
        {editStorageKind === "object_backed" && (
          <>
            <FormGroup label="Mid ObjectType">
              <HTMLSelect fill value={editMidObjectType} onChange={(e) => setEditMidObjectType(e.target.value)}>
                <option value="">Select…</option>
                {otOptions.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            <FormGroup label="Mid → source property">
              <InputGroup value={editMidSourceProperty} onChange={(e) => setEditMidSourceProperty(e.target.value)} />
            </FormGroup>
            <FormGroup label="Mid → target property">
              <InputGroup value={editMidTargetProperty} onChange={(e) => setEditMidTargetProperty(e.target.value)} />
            </FormGroup>
          </>
        )}
        <FormGroup label="Status">
          <HTMLSelect fill value={editLifecycleStatus} onChange={(e) => setEditLifecycleStatus(e.target.value)}>
            {LIFECYCLE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        {editLifecycleStatus === "deprecated" && (
          <>
            <FormGroup label="Deprecation reason">
              <InputGroup value={editDeprecationReason} onChange={(e) => setEditDeprecationReason(e.target.value)} />
            </FormGroup>
            <FormGroup label="Deprecation deadline">
              <InputGroup
                type="date"
                value={editDeprecationDeadline}
                onChange={(e) => setEditDeprecationDeadline(e.target.value)}
              />
            </FormGroup>
            <FormGroup label="Replacement URN">
              <InputGroup
                className="hl-mono"
                value={editReplacementUrn}
                onChange={(e) => setEditReplacementUrn(e.target.value)}
              />
            </FormGroup>
          </>
        )}
        <FormGroup label="Type classes (comma-separated)">
          <InputGroup value={editTypeClasses} onChange={(e) => setEditTypeClasses(e.target.value)} />
        </FormGroup>
        <FormGroup label="Source display name">
          <InputGroup value={editSourceDisplayName} onChange={(e) => setEditSourceDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Source plural display name">
          <InputGroup
            value={editSourcePluralDisplayName}
            onChange={(e) => setEditSourcePluralDisplayName(e.target.value)}
          />
        </FormGroup>
        <FormGroup label="Source API name">
          <InputGroup value={editSourceApiName} onChange={(e) => setEditSourceApiName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Source visibility">
          <HTMLSelect fill value={editSourceVisibility} onChange={(e) => setEditSourceVisibility(e.target.value)}>
            {VISIBILITIES.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Target display name">
          <InputGroup value={editTargetDisplayName} onChange={(e) => setEditTargetDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Target plural display name">
          <InputGroup
            value={editTargetPluralDisplayName}
            onChange={(e) => setEditTargetPluralDisplayName(e.target.value)}
          />
        </FormGroup>
        <FormGroup label="Target API name">
          <InputGroup value={editTargetApiName} onChange={(e) => setEditTargetApiName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Target visibility">
          <HTMLSelect fill value={editTargetVisibility} onChange={(e) => setEditTargetVisibility(e.target.value)}>
            {VISIBILITIES.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Project (optional)">
          <HTMLSelect fill value={editProjectUrn} onChange={(e) => setEditProjectUrn(e.target.value)}>
            <option value="">Workspace only</option>
            {projects.map((p) => (
              <option key={p.urn} value={p.urn}>
                {p.name}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
      </RegistryDialog>

      <div className="hl-ot-draft-footer">
        <Button
          minimal
          icon="arrow-left"
          onClick={() => void navigate({ to: "/ontology", search: { tab: "relation-types" } })}
        >
          Back to RelationTypes
        </Button>
        <Button icon="edit" onClick={openEdit}>
          Edit
        </Button>
      </div>
    </DetailPage>
  );
}
