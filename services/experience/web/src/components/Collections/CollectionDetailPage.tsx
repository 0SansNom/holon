import { useState } from "react";
import { useParams, useNavigate } from "@tanstack/react-router";
import { Button, ButtonGroup, Checkbox, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";
import { useCollection, useDeleteCollection, useObjectTypes, useApplications, useToggleCollectionMember } from "../../api/hooks";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { DetailPage } from "../common/PageLayout";
import { showSuccess } from "../../lib/toast";

function AddResourcesDialog({
  collectionId,
  memberUrns,
  onClose,
}: {
  collectionId: number;
  memberUrns: Set<string>;
  onClose: () => void;
}) {
  const { data: objectTypes } = useObjectTypes();
  const { data: applications } = useApplications();
  const toggle = useToggleCollectionMember();

  const candidates = [
    ...objectTypes.map((ot) => ({ urn: ot.urn, label: ot.name, kind: "Object type" })),
    ...applications.map((app) => ({ urn: app.urn, label: app.name, kind: "Application" })),
  ];

  return (
    <Dialog isOpen onClose={onClose} title="Add resources" className="hl-dialog-sm">
      <DialogBody>
        {candidates.length === 0 && <p className="hl-text-muted">No ObjectTypes or Applications exist yet.</p>}
        {candidates.map((c) => (
          <Checkbox
            key={c.urn}
            label={`${c.label} (${c.kind})`}
            checked={memberUrns.has(c.urn)}
            disabled={toggle.isPending}
            onChange={(e) => toggle.mutate({ collectionId, urn: c.urn, member: e.target.checked })}
          />
        ))}
      </DialogBody>
      <DialogFooter actions={<Button onClick={onClose}>Done</Button>} />
    </Dialog>
  );
}

export function CollectionDetailPage() {
  const { id } = useParams({ from: "/shell/collections/$id" });
  const collectionId = Number(id);
  const navigate = useNavigate();
  const { data: collection } = useCollection(collectionId);
  const { data: objectTypes } = useObjectTypes();
  const { data: applications } = useApplications();
  const deleteCollection = useDeleteCollection();
  const toggleMember = useToggleCollectionMember();
  const [adding, setAdding] = useState(false);

  if (!collection) return <p className="hl-text-muted">No collection with id {id}.</p>;

  const members = (collection.members ?? []).map((urn) => {
    const ot = objectTypes.find((o) => o.urn === urn);
    if (ot) return { urn, label: ot.name, kind: "Object type" };
    const app = applications.find((a) => a.urn === urn);
    if (app) return { urn, label: app.name, kind: "Application" };
    return { urn, label: urn, kind: "Unresolved" };
  });

  return (
    <DetailPage
      breadcrumbs={[{ label: "Collections", to: "/collections" }, { label: collection.name }]}
      title={collection.name}
      description={collection.description}
      actions={
        <ButtonGroup>
          <Button small icon="add" onClick={() => setAdding(true)}>
            Add resources
          </Button>
          <Button
            small
            icon="trash"
            intent="danger"
            minimal
            loading={deleteCollection.isPending}
            onClick={() => {
              deleteCollection.mutate(collectionId, {
                onSuccess: () => {
                  showSuccess(`Collection "${collection.name}" deleted`);
                  void navigate({ to: "/collections" });
                },
              });
            }}
          >
            Delete collection
          </Button>
        </ButtonGroup>
      }
    >
      <CardGrid minWidth={240}>
        {members.map((m) => (
          <div key={m.urn} className="hl-panel hl-flex-between hl-items-start">
            <div className="hl-min-w-0">
              <strong>{m.label}</strong>
              <div className="hl-text-muted-sm">{m.kind}</div>
            </div>
            <Button
              small
              minimal
              icon="cross"
              disabled={toggleMember.isPending}
              onClick={() => toggleMember.mutate({ collectionId, urn: m.urn, member: false })}
              title="Remove from this collection"
            />
          </div>
        ))}
        {members.length === 0 && (
          <EmptyState actionLabel="Add resources" onAction={() => setAdding(true)}>
            No resources yet — add ObjectTypes or Applications to this collection.
          </EmptyState>
        )}
      </CardGrid>

      {adding && (
        <AddResourcesDialog
          collectionId={collectionId}
          memberUrns={new Set(collection.members ?? [])}
          onClose={() => setAdding(false)}
        />
      )}
    </DetailPage>
  );
}
