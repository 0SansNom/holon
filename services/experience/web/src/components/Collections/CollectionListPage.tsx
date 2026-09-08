import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Button, Card, FormGroup, InputGroup } from "@blueprintjs/core";
import { useCollections, useCreateCollection } from "../../api/hooks";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { RegistryPage } from "../common/PageLayout";
import { useAsyncAction } from "../../hooks/useAsyncAction";

export function CollectionListPage() {
  const { data } = useCollections();
  const createCollection = useCreateCollection();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  function reset() {
    setName("");
    setDescription("");
  }

  function close() {
    setCreating(false);
    reset();
  }

  const { submit, error, isPending } = useAsyncAction(
    async () => {
      await createCollection.mutateAsync({ name, description });
      close();
    },
    { onSuccess: reset, successMessage: `Collection "${name}" created` },
  );

  return (
    <RegistryPage
      title="Collections"
      description="Named groups of resources you care about — across projects and tags. Add items from any resource’s ⋮ menu."
      actions={
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New collection
        </Button>
      }
    >
      <CardGrid minWidth={240}>
        {data.map((c) => (
          <Link key={c.id} to="/collections/$id" params={{ id: String(c.id) }} className="hl-link-reset">
            <Card interactive className="hl-h-full">
              <strong>{c.name}</strong>
              {c.description && <p className="hl-text-muted hl-mt-xs">{c.description}</p>}
            </Card>
          </Link>
        ))}
        {data.length === 0 && (
          <EmptyState actionLabel="New collection" onAction={() => setCreating(true)}>
            No collections yet — create one to curate resources across projects and tags.
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New collection"
        onClose={close}
        error={error}
        isPending={isPending}
        submitLabel="Create"
        submitDisabled={!name}
        onSubmit={() => submit(undefined)}
      >
        <FormGroup label="Name">
          <InputGroup placeholder="Q1 launch resources" value={name} onChange={(e) => setName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Description (optional)">
          <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
      </RegistryDialog>
    </RegistryPage>
  );
}
