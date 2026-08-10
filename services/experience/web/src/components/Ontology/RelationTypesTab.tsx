import { useState } from "react";
import { FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import { useRelationTypes, useCreateRelationType, useUpdateRelationType, useObjectTypes } from "../../api/hooks";
import type { RelationType } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { BranchesDialog } from "./BranchesDialog";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";

const CARDINALITIES = ["many_to_one", "one_to_many", "one_to_one", "many_to_many"] as const;

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
  const [editing, setEditing] = useState<RelationType | null>(null);
  const [editTargetProperty, setEditTargetProperty] = useState("");
  const [editCardinality, setEditCardinality] = useState<string>("many_to_one");
  const [branching, setBranching] = useState<RelationType | null>(null);

  usePaletteCreateIntent("create-relation-type", setCreating);

  function resetCreate() {
    setName("");
    setSourceObjectType("");
    setTargetObjectType("");
    setSourceProperty("");
    setTargetProperty("");
    setCardinality("many_to_one");
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
    });
    closeCreate();
  }, { successMessage: `Relation type "${name}" created` });

  function openEdit(rt: RelationType) {
    setEditing(rt);
    setEditTargetProperty(rt.target_property ?? "");
    setEditCardinality(rt.cardinality);
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    await updateRelationType.mutateAsync({
      name: editing.name,
      body: { target_property: editTargetProperty, cardinality: editCardinality },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Relation type"}" saved` });

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            A named, bidirectional link between two ObjectTypes — the foreign-key property lives on the source side,
            both ends are independently named (forward via Name, reverse via Target property), and the cardinality is
            spelled out explicitly, never implied.
          </>
        }
        createLabel="New relation type"
        onCreate={() => setCreating(true)}
      />

      <CardGrid minWidth={260}>
        {data.map((rt) => (
          <RegistryCard key={rt.urn} name={rt.name} onEdit={() => openEdit(rt)} onBranch={() => setBranching(rt)}>
            <div className="hl-text-muted-sm hl-mt-xs">
              {rt.source_object_type_urn.split(":").pop()} —({rt.source_property})→ {rt.target_object_type_urn.split(":").pop()}
            </div>
            {rt.target_property && (
              <div className="hl-text-muted-sm">
                ← {rt.target_object_type_urn.split(":").pop()}.{rt.target_property}
              </div>
            )}
            <Tag minimal className="hl-mt-xs">
              {rt.cardinality}
            </Tag>
          </RegistryCard>
        ))}
        {data.length === 0 && (
          <EmptyState actionLabel="New relation type" onAction={() => setCreating(true)}>
            No relation types yet.
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New relation type"
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!name || !sourceObjectType || !targetObjectType || !sourceProperty || !targetProperty}
        onSubmit={() => submitCreate(undefined)}
      >
        <FormGroup label="Name">
          <InputGroup placeholder="Order.customer" value={name} onChange={(e) => setName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Source ObjectType">
          <HTMLSelect fill value={sourceObjectType} onChange={(e) => setSourceObjectType(e.target.value)}>
            <option value="">Select…</option>
            {objectTypes.map((ot) => (
              <option key={ot.name} value={ot.name}>
                {ot.name}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Target ObjectType">
          <HTMLSelect fill value={targetObjectType} onChange={(e) => setTargetObjectType(e.target.value)}>
            <option value="">Select…</option>
            {objectTypes.map((ot) => (
              <option key={ot.name} value={ot.name}>
                {ot.name}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Source property (the foreign key)">
          <InputGroup placeholder="customerId" value={sourceProperty} onChange={(e) => setSourceProperty(e.target.value)} />
        </FormGroup>
        <FormGroup label="Target property (the reverse accessor)" helperText="What the target ObjectType calls this relation, e.g. Customer.orders">
          <InputGroup placeholder="orders" value={targetProperty} onChange={(e) => setTargetProperty(e.target.value)} />
        </FormGroup>
        <FormGroup label="Cardinality">
          <HTMLSelect fill value={cardinality} onChange={(e) => setCardinality(e.target.value)} options={[...CARDINALITIES]} />
        </FormGroup>
      </RegistryDialog>

      <RegistryDialog
        isOpen={editing !== null}
        title={`Edit ${editing?.name ?? ""}`}
        onClose={() => setEditing(null)}
        error={editError}
        isPending={editPending}
        submitLabel="Save"
        submitDisabled={!editTargetProperty}
        onSubmit={() => submitEdit(undefined)}
      >
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
          Source/target ObjectType and source property (<Tag minimal className="hl-mono">{editing?.source_property}</Tag>)
          aren't editable — they're the structural identity of the link.
        </p>
        <FormGroup
          label="Target property (the reverse accessor)"
          helperText="What the target ObjectType calls this relation, e.g. Customer.orders"
        >
          <InputGroup value={editTargetProperty} onChange={(e) => setEditTargetProperty(e.target.value)} />
        </FormGroup>
        <FormGroup label="Cardinality">
          <HTMLSelect
            fill
            value={editCardinality}
            onChange={(e) => setEditCardinality(e.target.value)}
            options={[...CARDINALITIES]}
          />
        </FormGroup>
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="relation_type"
          resourceName={branching.name}
          currentDefinition={{ target_property: branching.target_property, cardinality: branching.cardinality }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
