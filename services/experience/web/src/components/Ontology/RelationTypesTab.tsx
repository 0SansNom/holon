import { useState, type Dispatch, type SetStateAction } from "react";
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
import { urnShortName } from "../ObjectExplorer/objectExplorerUtils";
import { CARDINALITIES, STORAGE_KINDS, VISIBILITIES } from "./relationTypeConstants";
import { RelationTypeFormFields } from "./RelationTypeFormFields";
import {
  CREATE_STEPS,
  DEFAULT_RELATION_TYPE_FORM,
  defaultJoinDatasetName,
  defaultJoinSourceColumn,
  defaultJoinTargetColumn,
  isRelationTypeCreateStepValid,
  relationTypeBranchDefinition,
  relationTypeCreateBody,
  relationTypeFormFromRecord,
  relationTypeUpdateBody,
  type RelationTypeFormState,
} from "./relationTypeForm";

const LIFECYCLE_STATUSES = REGISTRY_LIFECYCLE_STATUSES;

function bindFormField(
  setForm: Dispatch<SetStateAction<RelationTypeFormState>>,
  key: keyof RelationTypeFormState,
) {
  return (event: { target: { value: string } }) => {
    setForm((prev) => ({ ...prev, [key]: event.target.value }));
  };
}

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
  const [createForm, setCreateForm] = useState<RelationTypeFormState>(DEFAULT_RELATION_TYPE_FORM);
  const [createStep, setCreateStep] = useState(0);
  const [editing, setEditing] = useState<RelationType | null>(null);
  const [editForm, setEditForm] = useState<RelationTypeFormState>(DEFAULT_RELATION_TYPE_FORM);
  const [deleting, setDeleting] = useState<RelationType | null>(null);
  const [branching, setBranching] = useState<RelationType | null>(null);

  usePaletteCreateIntent("create-relation-type", setCreating);

  function closeCreate() {
    setCreating(false);
    setCreateForm(DEFAULT_RELATION_TYPE_FORM);
    setCreateStep(0);
  }

  const {
    submit: submitCreate,
    error: createError,
    isPending: createPending,
  } = useAsyncAction(async () => {
    await createRelationType.mutateAsync(relationTypeCreateBody(createForm));
    closeCreate();
  }, { successMessage: `Relation type "${createForm.name}" created` });

  function openEdit(rt: RelationType) {
    setEditing(rt);
    setEditForm(relationTypeFormFromRecord(rt));
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    await updateRelationType.mutateAsync({
      name: editing.name,
      body: relationTypeUpdateBody(editForm, editing.project_urn ?? ""),
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
    const defaultName = defaultJoinDatasetName(createForm.sourceObjectType, createForm.targetObjectType);
    const srcCol = createForm.joinSourceColumn || defaultJoinSourceColumn(createForm.sourceObjectType);
    const tgtCol = createForm.joinTargetColumn || defaultJoinTargetColumn(createForm.targetObjectType);
    setGeneratingJoin(true);
    try {
      const created = await generateJoinDataset.mutateAsync({
        name: defaultName,
        source_column: srcCol,
        target_column: tgtCol,
      });
      setCreateForm((form) => ({
        ...form,
        joinDatasetUrn: created.dataset_urn,
        joinSourceColumn: created.source_column,
        joinTargetColumn: created.target_column,
      }));
    } finally {
      setGeneratingJoin(false);
    }
  }

  const otOptions = (objectTypes ?? []).map((ot) => ot.name);

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
        submitDisabled={!isRelationTypeCreateStepValid(createForm, createStep)}
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
              <InputGroup
                value={createForm.name}
                onChange={bindFormField(setCreateForm, "name")}
                placeholder="Order.customer"
              />
            </FormGroup>
            <FormGroup label="Source ObjectType">
              <HTMLSelect fill value={createForm.sourceObjectType} onChange={bindFormField(setCreateForm, "sourceObjectType")}>
                <option value="">Select…</option>
                {otOptions.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            <FormGroup label="Target ObjectType">
              <HTMLSelect fill value={createForm.targetObjectType} onChange={bindFormField(setCreateForm, "targetObjectType")}>
                <option value="">Select…</option>
                {otOptions.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            <FormGroup label="Cardinality">
              <HTMLSelect fill value={createForm.cardinality} onChange={bindFormField(setCreateForm, "cardinality")}>
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
              <HTMLSelect fill value={createForm.storageKind} onChange={bindFormField(setCreateForm, "storageKind")}>
                {STORAGE_KINDS.map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            {createForm.storageKind === "foreign_key" && (
              <FormGroup label="Source property (FK)">
                <InputGroup
                  value={createForm.sourceProperty}
                  onChange={bindFormField(setCreateForm, "sourceProperty")}
                />
              </FormGroup>
            )}
            {createForm.storageKind === "join_dataset" && (
              <>
                <Callout intent="primary" className="hl-mb-sm">
                  Generate an empty 2-column Iceberg join table, or paste an existing dataset URN.
                </Callout>
                <Button
                  icon="new-grid-item"
                  className="hl-mb-sm"
                  loading={generatingJoin || generateJoinDataset.isPending}
                  disabled={!createForm.sourceObjectType || !createForm.targetObjectType}
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
                  <InputGroup
                    value={createForm.joinDatasetUrn}
                    onChange={bindFormField(setCreateForm, "joinDatasetUrn")}
                  />
                </FormGroup>
                <FormGroup label="Join source column">
                  <InputGroup
                    value={createForm.joinSourceColumn}
                    onChange={bindFormField(setCreateForm, "joinSourceColumn")}
                  />
                </FormGroup>
                <FormGroup label="Join target column">
                  <InputGroup
                    value={createForm.joinTargetColumn}
                    onChange={bindFormField(setCreateForm, "joinTargetColumn")}
                  />
                </FormGroup>
              </>
            )}
            {createForm.storageKind === "object_backed" && (
              <>
                <FormGroup label="Mid ObjectType">
                  <HTMLSelect
                    fill
                    value={createForm.midObjectType}
                    onChange={bindFormField(setCreateForm, "midObjectType")}
                  >
                    <option value="">Select…</option>
                    {otOptions.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Mid → source property">
                  <InputGroup
                    value={createForm.midSourceProperty}
                    onChange={bindFormField(setCreateForm, "midSourceProperty")}
                  />
                </FormGroup>
                <FormGroup label="Mid → target property">
                  <InputGroup
                    value={createForm.midTargetProperty}
                    onChange={bindFormField(setCreateForm, "midTargetProperty")}
                  />
                </FormGroup>
              </>
            )}
            <FormGroup label="Target property (reverse accessor)">
              <InputGroup
                value={createForm.targetProperty}
                onChange={bindFormField(setCreateForm, "targetProperty")}
              />
            </FormGroup>
          </>
        )}
        {createStep === 2 && (
          <>
            <FormGroup label="Source display name">
              <InputGroup
                value={createForm.sourceDisplayName}
                onChange={bindFormField(setCreateForm, "sourceDisplayName")}
              />
            </FormGroup>
            <FormGroup label="Source plural display name">
              <InputGroup
                value={createForm.sourcePluralDisplayName}
                onChange={bindFormField(setCreateForm, "sourcePluralDisplayName")}
              />
            </FormGroup>
            <FormGroup label="Source API name">
              <InputGroup
                value={createForm.sourceApiName}
                onChange={bindFormField(setCreateForm, "sourceApiName")}
                placeholder="defaults to local name"
              />
            </FormGroup>
            <FormGroup label="Source visibility">
              <HTMLSelect
                fill
                value={createForm.sourceVisibility}
                onChange={bindFormField(setCreateForm, "sourceVisibility")}
              >
                {VISIBILITIES.map((v) => (
                  <option key={v} value={v}>
                    {v}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            <FormGroup label="Target display name">
              <InputGroup
                value={createForm.targetDisplayName}
                onChange={bindFormField(setCreateForm, "targetDisplayName")}
              />
            </FormGroup>
            <FormGroup label="Target plural display name">
              <InputGroup
                value={createForm.targetPluralDisplayName}
                onChange={bindFormField(setCreateForm, "targetPluralDisplayName")}
              />
            </FormGroup>
            <FormGroup label="Target API name">
              <InputGroup
                value={createForm.targetApiName}
                onChange={bindFormField(setCreateForm, "targetApiName")}
                placeholder="defaults to target property"
              />
            </FormGroup>
            <FormGroup label="Target visibility">
              <HTMLSelect
                fill
                value={createForm.targetVisibility}
                onChange={bindFormField(setCreateForm, "targetVisibility")}
              >
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
              <HTMLSelect
                fill
                value={createForm.lifecycleStatus}
                onChange={bindFormField(setCreateForm, "lifecycleStatus")}
              >
                {LIFECYCLE_STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            {createForm.lifecycleStatus === "deprecated" && (
              <>
                <FormGroup label="Deprecation reason">
                  <InputGroup
                    value={createForm.deprecationReason}
                    onChange={bindFormField(setCreateForm, "deprecationReason")}
                  />
                </FormGroup>
                <FormGroup label="Deprecation deadline">
                  <InputGroup
                    type="date"
                    value={createForm.deprecationDeadline}
                    onChange={bindFormField(setCreateForm, "deprecationDeadline")}
                  />
                </FormGroup>
                <FormGroup label="Replacement URN">
                  <InputGroup
                    className="hl-mono"
                    value={createForm.replacementUrn}
                    onChange={bindFormField(setCreateForm, "replacementUrn")}
                  />
                </FormGroup>
              </>
            )}
            <FormGroup label="Type classes (comma-separated)">
              <InputGroup
                value={createForm.typeClasses}
                onChange={bindFormField(setCreateForm, "typeClasses")}
                placeholder="hierarchy:parent, core"
              />
            </FormGroup>
            <FormGroup label="Project (optional)">
              <HTMLSelect fill value={createForm.projectUrn} onChange={bindFormField(setCreateForm, "projectUrn")}>
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
        <RelationTypeFormFields
          value={editForm}
          onChange={(patch) => setEditForm((form) => ({ ...form, ...patch }))}
          objectTypeNames={otOptions}
          projects={projects}
        />
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
          currentDefinition={relationTypeBranchDefinition(branching)}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
