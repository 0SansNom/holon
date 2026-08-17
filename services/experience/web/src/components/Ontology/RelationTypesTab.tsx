import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Button, Callout, Checkbox, FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import {
  useRelationTypes,
  useCreateRelationType,
  useUpdateRelationType,
  useDeleteRelationType,
  useRelationTypePermissions,
  useRelationTypeWritebackStatus,
  useGenerateJoinDataset,
  useObjectTypes,
  useProjects,
} from "../../api/hooks";
import type { RelationType } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { BranchesDialog } from "./BranchesDialog";
import { isEphemeralTestName } from "./ephemeralResources";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";
import { REGISTRY_LIFECYCLE_STATUSES } from "./lifecycleUtils";
import { parseTypeClassesInput } from "./typeClassUtils";
import { urnShortName } from "../ObjectExplorer/objectExplorerUtils";

// Exported: RelationTypeDetailPage.tsx shares these instead of redeclaring them.
export const CARDINALITIES = ["many_to_one", "one_to_many", "one_to_one", "many_to_many"] as const;
export const STORAGE_KINDS = ["foreign_key", "join_dataset", "object_backed"] as const;
export const VISIBILITIES = ["prominent", "normal", "hidden"] as const;
const LIFECYCLE_STATUSES = REGISTRY_LIFECYCLE_STATUSES;
const CREATE_STEPS = ["Ends", "Storage", "Side names", "Governance"] as const;

export function RelationPermissionsPanel({ name }: { name: string }) {
  const { data, isLoading, error } = useRelationTypePermissions(name, !!name);
  if (isLoading) return <p className="hl-text-muted-sm">Loading permissions…</p>;
  if (error) return <Callout intent="danger">Could not load permissions.</Callout>;
  if (!data) return null;
  return (
    <div className="hl-mt-sm">
      <Callout intent="primary" className="hl-mb-sm">
        Permissions cascade from workspace <Tag minimal>parent_workspace</Tag>
        {data.project_urn ? (
          <>
            {" "}
            + <Tag minimal>parent_project</Tag>
          </>
        ) : null}
        . Edit needs <Tag minimal>write</Tag>; delete needs <Tag minimal>approve</Tag>.
      </Callout>
      <FormGroup label="Parent workspace">
        <InputGroup className="hl-mono" readOnly value={data.parent_workspace_urn} />
      </FormGroup>
      {data.project_urn && (
        <FormGroup label="Project scope">
          <InputGroup className="hl-mono" readOnly value={data.project_urn} />
        </FormGroup>
      )}
      <div className="hl-tag-row">
        {(["read", "write", "approve"] as const).map((p) => (
          <Tag key={p} minimal intent={data.permissions[p] ? "success" : "none"}>
            {p}: {data.permissions[p] ? "yes" : "no"}
          </Tag>
        ))}
      </div>
    </div>
  );
}

export function RelationWritebackWarning({ name }: { name: string }) {
  const { data } = useRelationTypeWritebackStatus(name, !!name);
  if (!data?.has_writeback_risk && !(data?.warnings?.length)) return null;
  return (
    <Callout intent="warning" className="hl-mb-sm" icon="warning-sign">
      <div className="hl-section-title hl-mb-xs">Writeback / storage change risk</div>
      <ul className="hl-mb-0">
        {data.warnings.map((w) => (
          <li key={w}>{w}</li>
        ))}
      </ul>
    </Callout>
  );
}

export function RelationTypesTab() {
  const navigate = useNavigate();
  const { data } = useRelationTypes();
  const { data: objectTypes } = useObjectTypes();
  const { data: projects = [] } = useProjects();
  const createRelationType = useCreateRelationType();
  const updateRelationType = useUpdateRelationType();
  const deleteRelationType = useDeleteRelationType();
  const generateJoinDataset = useGenerateJoinDataset();
  const [generatingJoin, setGeneratingJoin] = useState(false);
  const [creating, setCreating] = useState(false);
  const [showEphemeral, setShowEphemeral] = useState(false);
  const ephemeralCount = (data ?? []).filter((rt) => isEphemeralTestName(rt.name)).length;
  const visibleRelations = showEphemeral
    ? (data ?? [])
    : (data ?? []).filter((rt) => !isEphemeralTestName(rt.name));
  const [name, setName] = useState("");
  const [sourceObjectType, setSourceObjectType] = useState("");
  const [targetObjectType, setTargetObjectType] = useState("");
  const [sourceProperty, setSourceProperty] = useState("");
  const [targetProperty, setTargetProperty] = useState("");
  const [cardinality, setCardinality] = useState<string>("many_to_one");
  const [storageKind, setStorageKind] = useState<string>("foreign_key");
  const [joinDatasetUrn, setJoinDatasetUrn] = useState("");
  const [joinSourceColumn, setJoinSourceColumn] = useState("");
  const [joinTargetColumn, setJoinTargetColumn] = useState("");
  const [midObjectType, setMidObjectType] = useState("");
  const [midSourceProperty, setMidSourceProperty] = useState("");
  const [midTargetProperty, setMidTargetProperty] = useState("");
  const [sourceDisplayName, setSourceDisplayName] = useState("");
  const [sourcePluralDisplayName, setSourcePluralDisplayName] = useState("");
  const [sourceApiName, setSourceApiName] = useState("");
  const [sourceVisibility, setSourceVisibility] = useState<string>("normal");
  const [targetDisplayName, setTargetDisplayName] = useState("");
  const [targetPluralDisplayName, setTargetPluralDisplayName] = useState("");
  const [targetApiName, setTargetApiName] = useState("");
  const [targetVisibility, setTargetVisibility] = useState<string>("normal");
  const [lifecycleStatus, setLifecycleStatus] = useState<string>("experimental");
  const [deprecationReason, setDeprecationReason] = useState("");
  const [deprecationDeadline, setDeprecationDeadline] = useState("");
  const [replacementUrn, setReplacementUrn] = useState("");
  const [typeClasses, setTypeClasses] = useState("");
  const [createProjectUrn, setCreateProjectUrn] = useState("");
  const [createStep, setCreateStep] = useState(0);
  const [editing, setEditing] = useState<RelationType | null>(null);
  const [editTargetProperty, setEditTargetProperty] = useState("");
  const [editCardinality, setEditCardinality] = useState<string>("many_to_one");
  const [editStorageKind, setEditStorageKind] = useState<string>("foreign_key");
  const [editJoinDatasetUrn, setEditJoinDatasetUrn] = useState("");
  const [editJoinSourceColumn, setEditJoinSourceColumn] = useState("");
  const [editJoinTargetColumn, setEditJoinTargetColumn] = useState("");
  const [editMidObjectType, setEditMidObjectType] = useState("");
  const [editMidSourceProperty, setEditMidSourceProperty] = useState("");
  const [editMidTargetProperty, setEditMidTargetProperty] = useState("");
  const [editSourceDisplayName, setEditSourceDisplayName] = useState("");
  const [editSourcePluralDisplayName, setEditSourcePluralDisplayName] = useState("");
  const [editSourceApiName, setEditSourceApiName] = useState("");
  const [editSourceVisibility, setEditSourceVisibility] = useState<string>("normal");
  const [editTargetDisplayName, setEditTargetDisplayName] = useState("");
  const [editTargetPluralDisplayName, setEditTargetPluralDisplayName] = useState("");
  const [editTargetApiName, setEditTargetApiName] = useState("");
  const [editTargetVisibility, setEditTargetVisibility] = useState<string>("normal");
  const [editLifecycleStatus, setEditLifecycleStatus] = useState<string>("experimental");
  const [editDeprecationReason, setEditDeprecationReason] = useState("");
  const [editDeprecationDeadline, setEditDeprecationDeadline] = useState("");
  const [editReplacementUrn, setEditReplacementUrn] = useState("");
  const [editTypeClasses, setEditTypeClasses] = useState("");
  const [editProjectUrn, setEditProjectUrn] = useState("");
  const [deleting, setDeleting] = useState<RelationType | null>(null);
  const [branching, setBranching] = useState<RelationType | null>(null);

  usePaletteCreateIntent("create-relation-type", setCreating);

  function resetCreate() {
    setName("");
    setSourceObjectType("");
    setTargetObjectType("");
    setSourceProperty("");
    setTargetProperty("");
    setCardinality("many_to_one");
    setStorageKind("foreign_key");
    setJoinDatasetUrn("");
    setJoinSourceColumn("");
    setJoinTargetColumn("");
    setMidObjectType("");
    setMidSourceProperty("");
    setMidTargetProperty("");
    setSourceDisplayName("");
    setSourcePluralDisplayName("");
    setSourceApiName("");
    setSourceVisibility("normal");
    setTargetDisplayName("");
    setTargetPluralDisplayName("");
    setTargetApiName("");
    setTargetVisibility("normal");
    setLifecycleStatus("experimental");
    setDeprecationReason("");
    setDeprecationDeadline("");
    setReplacementUrn("");
    setTypeClasses("");
    setCreateProjectUrn("");
    setCreateStep(0);
  }

  function closeCreate() {
    setCreating(false);
    resetCreate();
  }

  const {
    submit: submitCreate,
    error: createError,
    isPending: createPending,
  } = useAsyncAction(async () => {
    await createRelationType.mutateAsync({
      name,
      source_object_type: sourceObjectType,
      target_object_type: targetObjectType,
      source_property: sourceProperty,
      target_property: targetProperty,
      cardinality,
      storage_kind: storageKind,
      join_dataset_urn: joinDatasetUrn || undefined,
      join_source_column: joinSourceColumn || undefined,
      join_target_column: joinTargetColumn || undefined,
      mid_object_type: midObjectType || undefined,
      mid_source_property: midSourceProperty || undefined,
      mid_target_property: midTargetProperty || undefined,
      source_display_name: sourceDisplayName || undefined,
      source_plural_display_name: sourcePluralDisplayName || undefined,
      source_api_name: sourceApiName || undefined,
      source_visibility: sourceVisibility,
      target_display_name: targetDisplayName || undefined,
      target_plural_display_name: targetPluralDisplayName || undefined,
      target_api_name: targetApiName || undefined,
      target_visibility: targetVisibility,
      lifecycle_status: lifecycleStatus,
      deprecation_reason: lifecycleStatus === "deprecated" ? deprecationReason : undefined,
      deprecation_deadline: lifecycleStatus === "deprecated" ? deprecationDeadline || undefined : undefined,
      replacement_urn: lifecycleStatus === "deprecated" ? replacementUrn || undefined : undefined,
      type_classes: parseTypeClassesInput(typeClasses),
      project_urn: createProjectUrn || undefined,
    });
    closeCreate();
  }, { successMessage: `Relation type "${name}" created` });

  function openEdit(rt: RelationType) {
    setEditing(rt);
    setEditTargetProperty(rt.target_property ?? "");
    setEditCardinality(rt.cardinality);
    setEditStorageKind(rt.storage_kind ?? "foreign_key");
    setEditJoinDatasetUrn(rt.join_dataset_urn ?? "");
    setEditJoinSourceColumn(rt.join_source_column ?? "");
    setEditJoinTargetColumn(rt.join_target_column ?? "");
    setEditMidObjectType(rt.mid_object_type_urn ? urnShortName(rt.mid_object_type_urn) : "");
    setEditMidSourceProperty(rt.mid_source_property ?? "");
    setEditMidTargetProperty(rt.mid_target_property ?? "");
    setEditSourceDisplayName(rt.source_display_name ?? "");
    setEditSourcePluralDisplayName(rt.source_plural_display_name ?? "");
    setEditSourceApiName(rt.source_api_name || rt.name.split(".").at(-1) || "");
    setEditSourceVisibility(rt.source_visibility ?? "normal");
    setEditTargetDisplayName(rt.target_display_name ?? "");
    setEditTargetPluralDisplayName(rt.target_plural_display_name ?? "");
    setEditTargetApiName(rt.target_api_name || rt.target_property || "");
    setEditTargetVisibility(rt.target_visibility ?? "normal");
    setEditLifecycleStatus(rt.lifecycle_status ?? "experimental");
    setEditDeprecationReason(rt.deprecation_reason ?? "");
    setEditDeprecationDeadline((rt.deprecation_deadline ?? "").toString().slice(0, 10));
    setEditReplacementUrn(rt.replacement_urn ?? "");
    setEditTypeClasses((rt.type_classes ?? []).join(", "));
    setEditProjectUrn(rt.project_urn ?? "");
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    const previousProject = editing.project_urn ?? "";
    await updateRelationType.mutateAsync({
      name: editing.name,
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
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Relation type"}" saved` });

  const {
    submit: submitDelete,
    error: deleteError,
    isPending: deletePending,
  } = useAsyncAction(async () => {
    if (!deleting) return;
    const deletedName = deleting.name;
    await deleteRelationType.mutateAsync(deletedName);
    setDeleting(null);
  }, { successMessage: `Deleted "${deleting?.name ?? "relation type"}"` });

  async function handleGenerateJoinTable() {
    const defaultName = `${sourceObjectType || "source"}_${targetObjectType || "target"}_bridge`;
    const srcCol = joinSourceColumn || `${(sourceObjectType || "source").toLowerCase()}_id`;
    const tgtCol = joinTargetColumn || `${(targetObjectType || "target").toLowerCase()}_id`;
    setGeneratingJoin(true);
    try {
      const created = await generateJoinDataset.mutateAsync({
        name: defaultName,
        source_column: srcCol,
        target_column: tgtCol,
      });
      setJoinDatasetUrn(created.dataset_urn);
      setJoinSourceColumn(created.source_column);
      setJoinTargetColumn(created.target_column);
    } finally {
      setGeneratingJoin(false);
    }
  }

  const otOptions = (objectTypes ?? []).map((ot) => ot.name);

  function createStepValid(step: number): boolean {
    if (step === 0) return !!name.trim() && !!sourceObjectType && !!targetObjectType;
    if (step === 1) {
      if (storageKind === "foreign_key") return !!sourceProperty.trim();
      if (storageKind === "join_dataset") {
        return !!joinDatasetUrn.trim() && !!joinSourceColumn.trim() && !!joinTargetColumn.trim();
      }
      return !!midObjectType && !!midSourceProperty.trim() && !!midTargetProperty.trim();
    }
    return true;
  }

  function advanceCreate() {
    if (createStep < CREATE_STEPS.length - 1) {
      setCreateStep((s) => s + 1);
      return;
    }
    submitCreate(undefined);
  }

  return (
    <div>
      <OntologyTabHeader
        description={<>Bidirectional link types — FK, join-dataset (M:N), or object-backed.</>}
        onCreate={() => setCreating(true)}
        createLabel="Create relation type"
        trailing={
          ephemeralCount > 0 ? (
            <Checkbox
              checked={showEphemeral}
              label={`Show test leftovers (${ephemeralCount})`}
              onChange={(e) => setShowEphemeral(e.currentTarget.checked)}
              style={{ marginBottom: 0 }}
            />
          ) : undefined
        }
      />
      <CardGrid>
        {visibleRelations.map((rt) => (
          <RegistryCard
            key={rt.urn}
            name={rt.name}
            onEdit={() => openEdit(rt)}
            onBranch={() => setBranching(rt)}
            onDelete={() => setDeleting(rt)}
          >
            <div className="hl-tag-row hl-mt-xs">
              <Tag minimal>{rt.cardinality}</Tag>
              <Tag minimal>{rt.storage_kind ?? "foreign_key"}</Tag>
              <Tag minimal>{rt.lifecycle_status ?? "experimental"}</Tag>
              {rt.project_urn && (
                <Tag minimal icon="folder-close">
                  project
                </Tag>
              )}
            </div>
            <p className="hl-text-muted-sm hl-mono">
              {urnShortName(rt.source_object_type_urn)}.{rt.source_api_name || rt.source_property} ↔{" "}
              {urnShortName(rt.target_object_type_urn)}.{rt.target_api_name || rt.target_property}
            </p>
            <div className="hl-card-actions">
              <Button
                small
                minimal
                icon="document-open"
                onClick={() =>
                  void navigate({
                    to: "/ontology/relation-types/$name",
                    params: { name: rt.name },
                  })
                }
              >
                Open
              </Button>
            </div>
          </RegistryCard>
        ))}
        {visibleRelations.length === 0 && (
          <EmptyState actionLabel="Create relation type" onAction={() => setCreating(true)}>
            {(data ?? []).length === 0
              ? "No relation types yet."
              : "No durable relation types — show test leftovers to browse pytest links."}
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title={`Create relation type — ${CREATE_STEPS[createStep]} (${createStep + 1}/${CREATE_STEPS.length})`}
        onClose={closeCreate}
        onSubmit={advanceCreate}
        submitLabel={createStep === CREATE_STEPS.length - 1 ? "Create" : "Next"}
        submitDisabled={!createStepValid(createStep)}
        isPending={createPending}
        error={createError}
        footerStart={
          createStep > 0 ? (
            <Button minimal disabled={createPending} onClick={() => setCreateStep((s) => s - 1)}>
              Back
            </Button>
          ) : undefined
        }
      >
        <div className="hl-tag-row hl-mb-sm">
          {CREATE_STEPS.map((label, i) => (
            <Tag key={label} minimal intent={i === createStep ? "primary" : i < createStep ? "success" : "none"}>
              {i + 1}. {label}
            </Tag>
          ))}
        </div>
        {createStep === 0 && (
          <>
            <FormGroup label="Name">
              <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="Order.customer" />
            </FormGroup>
            <FormGroup label="Source ObjectType">
              <HTMLSelect fill value={sourceObjectType} onChange={(e) => setSourceObjectType(e.target.value)}>
                <option value="">Select…</option>
                {otOptions.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            <FormGroup label="Target ObjectType">
              <HTMLSelect fill value={targetObjectType} onChange={(e) => setTargetObjectType(e.target.value)}>
                <option value="">Select…</option>
                {otOptions.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            <FormGroup label="Cardinality">
              <HTMLSelect fill value={cardinality} onChange={(e) => setCardinality(e.target.value)}>
                {CARDINALITIES.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
          </>
        )}
        {createStep === 1 && (
          <>
            <FormGroup label="Storage kind">
              <HTMLSelect fill value={storageKind} onChange={(e) => setStorageKind(e.target.value)}>
                {STORAGE_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            {storageKind === "foreign_key" && (
              <FormGroup label="Source property (FK)">
                <InputGroup value={sourceProperty} onChange={(e) => setSourceProperty(e.target.value)} />
              </FormGroup>
            )}
            {storageKind === "join_dataset" && (
              <>
                <Callout intent="primary" className="hl-mb-sm">
                  Generate an empty 2-column Iceberg join table, or paste an existing dataset URN.
                </Callout>
                <Button
                  icon="new-grid-item"
                  className="hl-mb-sm"
                  loading={generatingJoin || generateJoinDataset.isPending}
                  disabled={!sourceObjectType || !targetObjectType}
                  onClick={() => void handleGenerateJoinTable()}
                >
                  Generate join table
                </Button>
                {generateJoinDataset.isError && (
                  <Callout intent="danger" className="hl-mb-sm">
                    {(generateJoinDataset.error as Error)?.message || "Generate failed"}
                  </Callout>
                )}
                <FormGroup label="Join dataset URN">
                  <InputGroup value={joinDatasetUrn} onChange={(e) => setJoinDatasetUrn(e.target.value)} />
                </FormGroup>
                <FormGroup label="Join source column">
                  <InputGroup value={joinSourceColumn} onChange={(e) => setJoinSourceColumn(e.target.value)} />
                </FormGroup>
                <FormGroup label="Join target column">
                  <InputGroup value={joinTargetColumn} onChange={(e) => setJoinTargetColumn(e.target.value)} />
                </FormGroup>
              </>
            )}
            {storageKind === "object_backed" && (
              <>
                <FormGroup label="Mid ObjectType">
                  <HTMLSelect fill value={midObjectType} onChange={(e) => setMidObjectType(e.target.value)}>
                    <option value="">Select…</option>
                    {otOptions.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Mid → source property">
                  <InputGroup value={midSourceProperty} onChange={(e) => setMidSourceProperty(e.target.value)} />
                </FormGroup>
                <FormGroup label="Mid → target property">
                  <InputGroup value={midTargetProperty} onChange={(e) => setMidTargetProperty(e.target.value)} />
                </FormGroup>
              </>
            )}
            <FormGroup label="Target property (reverse accessor)">
              <InputGroup value={targetProperty} onChange={(e) => setTargetProperty(e.target.value)} />
            </FormGroup>
          </>
        )}
        {createStep === 2 && (
          <>
            <FormGroup label="Source display name">
              <InputGroup value={sourceDisplayName} onChange={(e) => setSourceDisplayName(e.target.value)} />
            </FormGroup>
            <FormGroup label="Source plural display name">
              <InputGroup value={sourcePluralDisplayName} onChange={(e) => setSourcePluralDisplayName(e.target.value)} />
            </FormGroup>
            <FormGroup label="Source API name">
              <InputGroup value={sourceApiName} onChange={(e) => setSourceApiName(e.target.value)} placeholder="defaults to local name" />
            </FormGroup>
            <FormGroup label="Source visibility">
              <HTMLSelect fill value={sourceVisibility} onChange={(e) => setSourceVisibility(e.target.value)}>
                {VISIBILITIES.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            <FormGroup label="Target display name">
              <InputGroup value={targetDisplayName} onChange={(e) => setTargetDisplayName(e.target.value)} />
            </FormGroup>
            <FormGroup label="Target plural display name">
              <InputGroup value={targetPluralDisplayName} onChange={(e) => setTargetPluralDisplayName(e.target.value)} />
            </FormGroup>
            <FormGroup label="Target API name">
              <InputGroup value={targetApiName} onChange={(e) => setTargetApiName(e.target.value)} placeholder="defaults to target property" />
            </FormGroup>
            <FormGroup label="Target visibility">
              <HTMLSelect fill value={targetVisibility} onChange={(e) => setTargetVisibility(e.target.value)}>
                {VISIBILITIES.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
          </>
        )}
        {createStep === 3 && (
          <>
            <FormGroup label="Status">
              <HTMLSelect fill value={lifecycleStatus} onChange={(e) => setLifecycleStatus(e.target.value)}>
                {LIFECYCLE_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            {lifecycleStatus === "deprecated" && (
              <>
                <FormGroup label="Deprecation reason">
                  <InputGroup value={deprecationReason} onChange={(e) => setDeprecationReason(e.target.value)} />
                </FormGroup>
                <FormGroup label="Deprecation deadline">
                  <InputGroup type="date" value={deprecationDeadline} onChange={(e) => setDeprecationDeadline(e.target.value)} />
                </FormGroup>
                <FormGroup label="Replacement URN">
                  <InputGroup className="hl-mono" value={replacementUrn} onChange={(e) => setReplacementUrn(e.target.value)} />
                </FormGroup>
              </>
            )}
            <FormGroup label="Type classes (comma-separated)">
              <InputGroup
                value={typeClasses}
                onChange={(e) => setTypeClasses(e.target.value)}
                placeholder="hierarchy:parent, core"
              />
            </FormGroup>
            <FormGroup label="Project (optional)">
              <HTMLSelect fill value={createProjectUrn} onChange={(e) => setCreateProjectUrn(e.target.value)}>
                <option value="">Workspace only</option>
                {projects.map((p) => (
                  <option key={p.urn} value={p.urn}>
                    {p.name}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
          </>
        )}
      </RegistryDialog>

      <RegistryDialog
        isOpen={!!editing}
        title={`Edit ${editing?.name ?? ""}`}
        onClose={() => setEditing(null)}
        onSubmit={() => submitEdit(undefined)}
        submitLabel="Save"
        isPending={editPending}
        error={editError}
      >
        {editing && <RelationWritebackWarning name={editing.name} />}
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
              <InputGroup className="hl-mono" value={editReplacementUrn} onChange={(e) => setEditReplacementUrn(e.target.value)} />
            </FormGroup>
          </>
        )}
        <FormGroup label="Type classes (comma-separated)" helperText="e.g. hierarchy:parent">
          <InputGroup value={editTypeClasses} onChange={(e) => setEditTypeClasses(e.target.value)} placeholder="hierarchy:parent" />
        </FormGroup>
        <FormGroup label="Source display name">
          <InputGroup value={editSourceDisplayName} onChange={(e) => setEditSourceDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Source plural display name">
          <InputGroup value={editSourcePluralDisplayName} onChange={(e) => setEditSourcePluralDisplayName(e.target.value)} />
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
          <InputGroup value={editTargetPluralDisplayName} onChange={(e) => setEditTargetPluralDisplayName(e.target.value)} />
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
        {editing && <RelationPermissionsPanel name={editing.name} />}
      </RegistryDialog>

      <RegistryDialog
        isOpen={!!deleting}
        title={`Delete ${deleting?.name ?? ""}`}
        onClose={() => setDeleting(null)}
        onSubmit={() => submitDelete(undefined)}
        submitLabel="Delete"
        intent="danger"
        isPending={deletePending}
        error={deleteError}
      >
        <p>
          Delete relation type <Tag minimal className="hl-mono">{deleting?.name}</Tag>? Traversal via this link
          will stop resolving immediately.
          {(deleting?.lifecycle_status ?? "experimental") === "active" && (
            <> Set status to deprecated first — active link types cannot be deleted.</>
          )}
        </p>
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="relation_type"
          resourceName={branching.name}
          currentDefinition={{
            source_object_type: urnShortName(branching.source_object_type_urn),
            target_object_type: urnShortName(branching.target_object_type_urn),
            source_object_type_urn: branching.source_object_type_urn,
            target_object_type_urn: branching.target_object_type_urn,
            source_property: branching.source_property,
            target_property: branching.target_property,
            cardinality: branching.cardinality,
            storage_kind: branching.storage_kind ?? "foreign_key",
            join_dataset_urn: branching.join_dataset_urn ?? null,
            join_source_column: branching.join_source_column ?? null,
            join_target_column: branching.join_target_column ?? null,
            mid_object_type_urn: branching.mid_object_type_urn ?? null,
            mid_object_type: branching.mid_object_type_urn ? urnShortName(branching.mid_object_type_urn) : null,
            mid_source_property: branching.mid_source_property ?? null,
            mid_target_property: branching.mid_target_property ?? null,
            source_display_name: branching.source_display_name ?? "",
            source_plural_display_name: branching.source_plural_display_name ?? "",
            source_api_name: branching.source_api_name ?? "",
            source_visibility: branching.source_visibility ?? "normal",
            target_display_name: branching.target_display_name ?? "",
            target_plural_display_name: branching.target_plural_display_name ?? "",
            target_api_name: branching.target_api_name ?? "",
            target_visibility: branching.target_visibility ?? "normal",
            lifecycle_status: branching.lifecycle_status ?? "experimental",
            type_classes: branching.type_classes ?? [],
            project_urn: branching.project_urn ?? null,
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
