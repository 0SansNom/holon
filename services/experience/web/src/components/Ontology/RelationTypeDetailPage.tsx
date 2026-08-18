import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { Button, Tab, Tabs, Tag, type TabId } from "@blueprintjs/core";
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
import { RelationPermissionsPanel, RelationWritebackWarning } from "./RelationTypesTab";
import { RelationTypeFormFields } from "./RelationTypeFormFields";
import {
  DEFAULT_RELATION_TYPE_FORM,
  relationTypeBranchDefinition,
  relationTypeFormFromRecord,
  relationTypeUpdateBody,
  type RelationTypeFormState,
} from "./relationTypeForm";
import { urnShortName } from "../ObjectExplorer/objectExplorerUtils";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useOntologyDiscoverStore } from "../../store/ontologyDiscover";

type DetailTab = "overview" | "datasources";

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
  const [editForm, setEditForm] = useState<RelationTypeFormState>(DEFAULT_RELATION_TYPE_FORM);

  const recordVisit = useOntologyDiscoverStore((s) => s.recordVisit);
  const isFavorite = useOntologyDiscoverStore((s) => s.isFavorite("relation_type", name));
  const toggleFavorite = useOntologyDiscoverStore((s) => s.toggleFavorite);

  useEffect(() => {
    if (relationType) recordVisit("relation_type", relationType.name);
  }, [relationType, recordVisit]);

  function openEdit() {
    if (!relationType) return;
    setEditForm(relationTypeFormFromRecord(relationType));
    setEditing(true);
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!relationType) return;
    await updateRelationType.mutateAsync({
      name: relationType.name,
      body: relationTypeUpdateBody(editForm, relationType.project_urn ?? ""),
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
          currentDefinition={relationTypeBranchDefinition(relationType)}
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
        <RelationTypeFormFields
          value={editForm}
          onChange={(patch) => setEditForm((form) => ({ ...form, ...patch }))}
          objectTypeNames={otOptions}
          projects={projects}
        />
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
