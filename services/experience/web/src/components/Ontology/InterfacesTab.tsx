import { useState } from "react";
import { FormGroup, InputGroup, Tag, TagInput } from "@blueprintjs/core";
import { useInterfaces, useCreateInterface, useUpdateInterface } from "../../api/hooks";
import type { InterfaceType } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { BranchesDialog } from "./BranchesDialog";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";

export function InterfacesTab() {
  const { data } = useInterfaces();
  const createInterface = useCreateInterface();
  const updateInterface = useUpdateInterface();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [requiredProperties, setRequiredProperties] = useState<string[]>([]);
  const [requiredActions, setRequiredActions] = useState<string[]>([]);
  const [description, setDescription] = useState("");
  const [editing, setEditing] = useState<InterfaceType | null>(null);
  const [editRequiredProperties, setEditRequiredProperties] = useState<string[]>([]);
  const [editRequiredActions, setEditRequiredActions] = useState<string[]>([]);
  const [editDescription, setEditDescription] = useState("");
  const [branching, setBranching] = useState<InterfaceType | null>(null);

  usePaletteCreateIntent("create-interface", setCreating);

  function resetCreate() {
    setName("");
    setRequiredProperties([]);
    setRequiredActions([]);
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
    await createInterface.mutateAsync({
      name,
      required_properties: requiredProperties,
      required_actions: requiredActions,
      description: description || undefined,
    });
    closeCreate();
  }, { successMessage: `Interface "${name}" created` });

  function openEdit(iface: InterfaceType) {
    setEditing(iface);
    setEditRequiredProperties(iface.required_properties);
    setEditRequiredActions(iface.required_actions);
    setEditDescription(iface.description ?? "");
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    await updateInterface.mutateAsync({
      name: editing.name,
      body: {
        required_properties: editRequiredProperties,
        required_actions: editRequiredActions,
        description: editDescription,
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Interface"}" saved` });

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            A named, checked contract — an ObjectType declaring <code>implements</code> must actually have every
            required property mapped and every required Action defined, checked at publish time, not just a label.
          </>
        }
        createLabel="New interface"
        onCreate={() => setCreating(true)}
      />

      <CardGrid>
        {data.map((iface) => (
          <RegistryCard key={iface.name} name={iface.name} onEdit={() => openEdit(iface)} onBranch={() => setBranching(iface)}>
            {iface.required_properties.length > 0 && (
              <div className="hl-mt-xs">
                <div className="hl-text-muted-sm">Requires properties</div>
                <div className="hl-tag-row hl-mt-xs">
                  {iface.required_properties.map((p) => (
                    <Tag key={p} minimal>
                      {p}
                    </Tag>
                  ))}
                </div>
              </div>
            )}
            {iface.required_actions.length > 0 && (
              <div className="hl-mt-xs">
                <div className="hl-text-muted-sm">Requires actions</div>
                <div className="hl-tag-row hl-mt-xs">
                  {iface.required_actions.map((a) => (
                    <Tag key={a} minimal icon="lightning">
                      {a}
                    </Tag>
                  ))}
                </div>
              </div>
            )}
            {iface.description && <p className="hl-card-desc">{iface.description}</p>}
          </RegistryCard>
        ))}
        {data.length === 0 && (
          <EmptyState actionLabel="New interface" onAction={() => setCreating(true)}>
            No interfaces yet.
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New interface"
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!name}
        onSubmit={() => submitCreate(undefined)}
      >
        <FormGroup label="Name">
          <InputGroup placeholder="Contactable" value={name} onChange={(e) => setName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Required properties">
          <TagInput
            placeholder="type a property name, press Enter"
            values={requiredProperties}
            onChange={(values) => setRequiredProperties(values as string[])}
          />
        </FormGroup>
        <FormGroup label="Required actions">
          <TagInput
            placeholder="type an action's local name, press Enter"
            values={requiredActions}
            onChange={(values) => setRequiredActions(values as string[])}
          />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup placeholder="Anything with a reachable contact method" value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
      </RegistryDialog>

      <RegistryDialog
        isOpen={editing !== null}
        title={`Edit ${editing?.name ?? ""}`}
        onClose={() => setEditing(null)}
        error={editError}
        isPending={editPending}
        submitLabel="Save"
        onSubmit={() => submitEdit(undefined)}
      >
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
          Name isn't editable — it's the key referenced from every ObjectType's <code>implements</code> list.
        </p>
        <FormGroup label="Required properties">
          <TagInput
            placeholder="type a property name, press Enter"
            values={editRequiredProperties}
            onChange={(values) => setEditRequiredProperties(values as string[])}
          />
        </FormGroup>
        <FormGroup label="Required actions">
          <TagInput
            placeholder="type an action's local name, press Enter"
            values={editRequiredActions}
            onChange={(values) => setEditRequiredActions(values as string[])}
          />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup value={editDescription} onChange={(e) => setEditDescription(e.target.value)} />
        </FormGroup>
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="interface_type"
          resourceName={branching.name}
          currentDefinition={{
            required_properties: branching.required_properties,
            required_actions: branching.required_actions,
            description: branching.description,
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
