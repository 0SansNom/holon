import { useState } from "react";
import { Callout, FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import { useSharedPropertyTypes, useCreateSharedPropertyType, useUpdateSharedPropertyType, useValueTypes } from "../../api/hooks";
import type { SharedPropertyType } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { BranchesDialog } from "./BranchesDialog";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";

export function SharedPropertyTypesTab() {
  const { data } = useSharedPropertyTypes();
  const { data: valueTypes } = useValueTypes();
  const createSharedPropertyType = useCreateSharedPropertyType();
  const updateSharedPropertyType = useUpdateSharedPropertyType();
  const [creating, setCreating] = useState(false);
  const [apiName, setApiName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [valueType, setValueType] = useState("");
  const [description, setDescription] = useState("");
  const [editing, setEditing] = useState<SharedPropertyType | null>(null);
  const [editDisplayName, setEditDisplayName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [branching, setBranching] = useState<SharedPropertyType | null>(null);

  usePaletteCreateIntent("create-shared-property-type", setCreating);

  function resetCreate() {
    setApiName("");
    setDisplayName("");
    setValueType("");
    setDescription("");
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
    await createSharedPropertyType.mutateAsync({
      api_name: apiName,
      display_name: displayName,
      value_type: valueType,
      description: description || undefined,
    });
    closeCreate();
  }, { successMessage: `Shared property type "${displayName}" created` });

  function openEdit(spt: SharedPropertyType) {
    setEditing(spt);
    setEditDisplayName(spt.display_name);
    setEditDescription(spt.description ?? "");
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    await updateSharedPropertyType.mutateAsync({
      apiName: editing.api_name,
      body: { display_name: editDisplayName, description: editDescription },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.display_name ?? "Shared property type"}" saved` });

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            A canonical, reusable <em>property</em> definition — an API name plus a display name and description,
            wrapping a Value Type for its data shape. Reference it from any ObjectType's <code>property_types</code>{" "}
            (<code>{"{kind: \"shared_property_type\", shared_property_type: \"…\"}"}</code>) so renaming or
            redescribing the property is a single edit, not one per ObjectType.
          </>
        }
        createLabel="New shared property type"
        createDisabled={valueTypes.length === 0}
        onCreate={() => setCreating(true)}
      />

      {valueTypes.length === 0 && (
        <Callout intent="none" style={{ marginBottom: 12 }}>
          Register a Value Type first (the "Value Types" tab) — a Shared Property Type always wraps one.
        </Callout>
      )}

      <CardGrid minWidth={260}>
        {data.map((spt) => (
          <RegistryCard
            key={spt.api_name}
            name={spt.display_name}
            onEdit={() => openEdit(spt)}
            onBranch={() => setBranching(spt)}
          >
            <div className="hl-tag-row hl-mt-xs">
              <Tag minimal className="hl-mono">
                {spt.api_name}
              </Tag>
              <Tag minimal icon="link">
                {spt.value_type}
              </Tag>
            </div>
            {spt.description && <p className="hl-card-desc">{spt.description}</p>}
          </RegistryCard>
        ))}
        {data.length === 0 && (
          <EmptyState actionLabel="New shared property type" onAction={() => setCreating(true)}>
            No shared property types yet.
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New shared property type"
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!apiName || !displayName || !valueType}
        onSubmit={() => submitCreate(undefined)}
      >
        <FormGroup label="API name" helperText="referenced by property_types">
          <InputGroup className="hl-mono" placeholder="email" value={apiName} onChange={(e) => setApiName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Display name">
          <InputGroup placeholder="Email address" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Value type">
          <HTMLSelect fill value={valueType} onChange={(e) => setValueType(e.target.value)}>
            <option value="">Select…</option>
            {valueTypes.map((vt) => (
              <option key={vt.name} value={vt.name}>
                {vt.name}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup
            placeholder="the canonical contact email property"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormGroup>
      </RegistryDialog>

      <RegistryDialog
        isOpen={editing !== null}
        title={`Edit ${editing?.api_name ?? ""}`}
        onClose={() => setEditing(null)}
        error={editError}
        isPending={editPending}
        submitLabel="Save"
        submitDisabled={!editDisplayName}
        onSubmit={() => submitEdit(undefined)}
      >
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
          API name (<Tag minimal className="hl-mono">{editing?.api_name}</Tag>) and wrapped value type (
          <Tag minimal icon="link">{editing?.value_type}</Tag>) aren't editable — changing either would silently
          change the data contract for every property referencing this Shared Property Type.
        </p>
        <FormGroup label="Display name">
          <InputGroup value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup value={editDescription} onChange={(e) => setEditDescription(e.target.value)} />
        </FormGroup>
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="shared_property_type"
          resourceName={branching.api_name}
          currentDefinition={{
            display_name: branching.display_name,
            value_type: branching.value_type,
            description: branching.description,
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
