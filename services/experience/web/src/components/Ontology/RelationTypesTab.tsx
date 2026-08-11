import { useState } from "react";
import { FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import {
  useRelationTypes,
  useCreateRelationType,
  useUpdateRelationType,
  useObjectTypes,
} from "../../api/hooks";
import type { RelationType } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { BranchesDialog } from "./BranchesDialog";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";

const CARDINALITIES = ["many_to_one", "one_to_many", "one_to_one", "many_to_many"] as const;
const STORAGE_KINDS = ["foreign_key", "join_dataset", "object_backed"] as const;

function localName(urn: string): string {
  return urn.split(":").at(-1) ?? urn;
}

export function RelationTypesTab() {
  const { data } = useRelationTypes();
  const { data: objectTypes } = useObjectTypes();
  const createRelationType = useCreateRelationType();
  const updateRelationType = useUpdateRelationType();
  const [creating, setCreating] = useState(false);
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
  const [editing, setEditing] = useState<RelationType | null>(null);
  const [editTargetProperty, setEditTargetProperty] = useState("");
  const [editCardinality, setEditCardinality] = useState<string>("many_to_one");
  const [editStorageKind, setEditStorageKind] = useState<string>("foreign_key");
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
    });
    closeCreate();
  }, { successMessage: `Relation type "${name}" created` });

  function openEdit(rt: RelationType) {
    setEditing(rt);
    setEditTargetProperty(rt.target_property ?? "");
    setEditCardinality(rt.cardinality);
    setEditStorageKind(rt.storage_kind ?? "foreign_key");
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    await updateRelationType.mutateAsync({
      name: editing.name,
      body: {
        target_property: editTargetProperty,
        cardinality: editCardinality,
        storage_kind: editStorageKind,
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Relation type"}" saved` });

  const otOptions = (objectTypes ?? []).map((ot) => ot.name);

  return (
    <div>
      <OntologyTabHeader
        description={<>Bidirectional link types — FK, join-dataset (M:N), or object-backed.</>}
        onCreate={() => setCreating(true)}
        createLabel="Create relation type"
      />
      <CardGrid>
        {(data ?? []).map((rt) => (
          <RegistryCard
            key={rt.urn}
            name={rt.name}
            onEdit={() => openEdit(rt)}
            onBranch={() => setBranching(rt)}
          >
            <div className="hl-tag-row hl-mt-xs">
              <Tag minimal>{rt.cardinality}</Tag>
              <Tag minimal>{rt.storage_kind ?? "foreign_key"}</Tag>
            </div>
            <p className="hl-text-muted-sm hl-mono">
              {localName(rt.source_object_type_urn)}.{rt.source_property} ↔ {localName(rt.target_object_type_urn)}.
              {rt.target_property}
            </p>
          </RegistryCard>
        ))}
        {(data ?? []).length === 0 && (
          <EmptyState actionLabel="Create relation type" onAction={() => setCreating(true)}>
            No relation types yet.
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="Create relation type"
        onClose={closeCreate}
        onSubmit={() => submitCreate(undefined)}
        submitLabel="Create"
        isPending={createPending}
        error={createError}
      >
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
        <FormGroup label="Cardinality">
          <HTMLSelect fill value={cardinality} onChange={(e) => setCardinality(e.target.value)}>
            {CARDINALITIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
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
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="relation_type"
          resourceName={branching.name}
          currentDefinition={{
            source_property: branching.source_property,
            target_property: branching.target_property,
            cardinality: branching.cardinality,
            storage_kind: branching.storage_kind ?? "foreign_key",
            join_dataset_urn: branching.join_dataset_urn ?? null,
            join_source_column: branching.join_source_column ?? null,
            join_target_column: branching.join_target_column ?? null,
            mid_object_type_urn: branching.mid_object_type_urn ?? null,
            mid_source_property: branching.mid_source_property ?? null,
            mid_target_property: branching.mid_target_property ?? null,
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
