import { useState } from "react";
import { Checkbox, FormGroup, InputGroup, Tag } from "@blueprintjs/core";
import {
  useObjectTypeGroups,
  useCreateObjectTypeGroup,
  useUpdateObjectTypeGroup,
  useDeleteObjectTypeGroup,
  useObjectTypes,
} from "../../api/hooks";
import type { ObjectTypeGroup } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";

export function ObjectTypeGroupsTab() {
  const { data } = useObjectTypeGroups();
  const { data: objectTypes } = useObjectTypes();
  const createGroup = useCreateObjectTypeGroup();
  const updateGroup = useUpdateObjectTypeGroup();
  const deleteGroup = useDeleteObjectTypeGroup();
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ObjectTypeGroup | null>(null);
  const [deleting, setDeleting] = useState<ObjectTypeGroup | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [membersSet, setMembersSet] = useState<Set<string>>(new Set());
  const [editDescription, setEditDescription] = useState("");
  const [editMembers, setEditMembers] = useState<Set<string>>(new Set());

  usePaletteCreateIntent("create-object-type-group", setCreating);

  function toggle(set: Set<string>, setSet: (s: Set<string>) => void, value: string) {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    setSet(next);
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

  function openEdit(group: ObjectTypeGroup) {
    setEditing(group);
    setEditDescription(group.description ?? "");
    setEditMembers(new Set(group.object_types));
  }

  const {
    submit: submitCreate,
    error: createError,
    isPending: createPending,
  } = useAsyncAction(async () => {
    await createGroup.mutateAsync({ name, description: description || undefined, object_types: [...membersSet] });
    closeCreate();
  }, { successMessage: `Group "${name}" created` });

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    await updateGroup.mutateAsync({
      name: editing.name,
      body: {
        name: editing.name,
        description: editDescription || undefined,
        object_types: [...editMembers],
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Group"}" saved` });

  const {
    submit: submitDelete,
    error: deleteError,
    isPending: deletePending,
  } = useAsyncAction(async () => {
    if (!deleting) return;
    await deleteGroup.mutateAsync(deleting.name);
    setDeleting(null);
  }, { successMessage: `Group "${deleting?.name ?? ""}" deleted` });

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
          <RegistryCard
            key={group.name}
            name={group.name}
            onEdit={() => openEdit(group)}
            onDelete={() => setDeleting(group)}
          >
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
            <Checkbox
              key={ot.name}
              label={ot.name}
              checked={membersSet.has(ot.name)}
              onChange={() => toggle(membersSet, setMembersSet, ot.name)}
            />
          ))}
          {objectTypes.length === 0 && <p className="hl-text-muted-sm">No ObjectTypes yet.</p>}
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
        <FormGroup label="Description">
          <InputGroup value={editDescription} onChange={(e) => setEditDescription(e.target.value)} />
        </FormGroup>
        <FormGroup label="ObjectTypes">
          {objectTypes.map((ot) => (
            <Checkbox
              key={ot.name}
              label={ot.name}
              checked={editMembers.has(ot.name)}
              onChange={() => toggle(editMembers, setEditMembers, ot.name)}
            />
          ))}
        </FormGroup>
      </RegistryDialog>

      <RegistryDialog
        isOpen={deleting !== null}
        title={`Delete ${deleting?.name ?? ""}`}
        onClose={() => setDeleting(null)}
        error={deleteError}
        isPending={deletePending}
        submitLabel="Delete"
        intent="danger"
        onSubmit={() => submitDelete(undefined)}
      >
        <p>
          Delete group <Tag minimal>{deleting?.name}</Tag>? ObjectTypes are not deleted — only the navigational
          cluster.
        </p>
      </RegistryDialog>
    </div>
  );
}
