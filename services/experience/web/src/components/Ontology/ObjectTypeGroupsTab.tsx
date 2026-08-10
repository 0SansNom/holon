import { useState } from "react";
import { Checkbox, FormGroup, InputGroup, Tag } from "@blueprintjs/core";
import { useObjectTypeGroups, useCreateObjectTypeGroup, useObjectTypes } from "../../api/hooks";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";

export function ObjectTypeGroupsTab() {
  const { data } = useObjectTypeGroups();
  const { data: objectTypes } = useObjectTypes();
  const createGroup = useCreateObjectTypeGroup();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [membersSet, setMembersSet] = useState<Set<string>>(new Set());

  usePaletteCreateIntent("create-object-type-group", setCreating);

  function toggle(value: string) {
    const next = new Set(membersSet);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    setMembersSet(next);
  }

  function resetCreate() {
    setName("");
    setDescription("");
    setMembersSet(new Set());
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
    await createGroup.mutateAsync({ name, description: description || undefined, object_types: [...membersSet] });
    closeCreate();
  }, { successMessage: `Group "${name}" created` });

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            A named, navigational cluster of ObjectTypes (e.g. "Customer-facing") — purely organizational, no new
            permission or schema concept. Use the group filter on the ObjectTypes tab to narrow the grid.
          </>
        }
        createLabel="New group"
        onCreate={() => setCreating(true)}
      />

      <CardGrid>
        {data.map((group) => (
          <RegistryCard key={group.name} name={group.name}>
            <div className="hl-tag-row hl-mt-xs">
              {group.object_types.map((ot) => (
                <Tag key={ot} minimal>
                  {ot}
                </Tag>
              ))}
            </div>
            {group.description && <p className="hl-card-desc">{group.description}</p>}
          </RegistryCard>
        ))}
        {data.length === 0 && (
          <EmptyState actionLabel="New group" onAction={() => setCreating(true)}>
            No object type groups yet.
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New object type group"
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!name}
        onSubmit={() => submitCreate(undefined)}
      >
        <FormGroup label="Name">
          <InputGroup placeholder="Customer-facing" value={name} onChange={(e) => setName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup
            placeholder="Types touching the customer relationship"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </FormGroup>
        <FormGroup label="ObjectTypes">
          {objectTypes.map((ot) => (
            <Checkbox key={ot.name} label={ot.name} checked={membersSet.has(ot.name)} onChange={() => toggle(ot.name)} />
          ))}
          {objectTypes.length === 0 && <p className="hl-text-muted-sm">No ObjectTypes yet.</p>}
        </FormGroup>
      </RegistryDialog>
    </div>
  );
}
