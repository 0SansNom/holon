import { Suspense, useState } from "react";
import { Button, Checkbox, Dialog, DialogBody, DialogFooter, Menu, MenuItem, PopoverNext, Spinner, Tag, TagInput } from "@blueprintjs/core";
import {
  useResourceTags,
  useSetResourceTags,
  useSetFeatured,
  useCollections,
  useResourceCollections,
  useToggleCollectionMember,
} from "../../api/hooks";

// Resource-kind-agnostic — takes only a URN, so it drops unchanged into
// any card/row for a resource kind the backend has hardened ReBAC for
// (see experience/app/main.py's `_RESOURCE_AUTHZ_TYPE`; currently
// ObjectTypes and Applications, more as their own authz lands).
export function ResourceActionsMenu({ urn }: { urn: string }) {
  const { data } = useResourceTags();
  const current = data?.find((r) => r.resource_urn === urn);
  const tags = current?.tags ?? [];
  const featured = current?.featured ?? false;

  const setTags = useSetResourceTags();
  const setFeatured = useSetFeatured();
  const [editingTags, setEditingTags] = useState(false);
  const [draftTags, setDraftTags] = useState<string[]>(tags);
  const [editingCollections, setEditingCollections] = useState(false);

  return (
    <>
      <PopoverNext
        placement="bottom-end"
        content={
          <Menu>
            <MenuItem icon="clipboard" text="Copy URN" onClick={() => void navigator.clipboard.writeText(urn)} />
            <MenuItem
              icon="tag"
              text="Edit tags…"
              onClick={() => {
                setDraftTags(tags);
                setEditingTags(true);
              }}
            />
            <MenuItem
              icon={featured ? "star" : "star-empty"}
              text={featured ? "Remove Promoted" : "Mark as Promoted"}
              disabled={setFeatured.isPending}
              onClick={() => setFeatured.mutate({ urn, featured: !featured })}
            />
            <MenuItem icon="layers" text="Add to collections…" onClick={() => setEditingCollections(true)} />
          </Menu>
        }
      >
        <Button small minimal icon="more" />
      </PopoverNext>

      <Dialog isOpen={editingTags} onClose={() => setEditingTags(false)} title="Edit tags" style={{ width: 440 }}>
        <DialogBody>
          <TagInput
            placeholder="type a tag, press Enter"
            values={draftTags}
            onChange={(values) => setDraftTags(values as string[])}
          />
        </DialogBody>
        <DialogFooter
          actions={
            <Button
              intent="primary"
              loading={setTags.isPending}
              onClick={() => {
                setTags.mutate(
                  { urn, tags: draftTags },
                  { onSuccess: () => setEditingTags(false) },
                );
              }}
            >
              Save
            </Button>
          }
        />
      </Dialog>

      {editingCollections && <EditCollectionsDialog urn={urn} onClose={() => setEditingCollections(false)} />}
    </>
  );
}

function EditCollectionsDialog({ urn, onClose }: { urn: string; onClose: () => void }) {
  return (
    <Dialog isOpen onClose={onClose} title="Add to collections" style={{ width: 400 }}>
      <Suspense fallback={<DialogBody><Spinner size={24} /></DialogBody>}>
        <EditCollectionsDialogBody urn={urn} onClose={onClose} />
      </Suspense>
    </Dialog>
  );
}

function EditCollectionsDialogBody({ urn, onClose }: { urn: string; onClose: () => void }) {
  const { data: allCollections } = useCollections();
  const { data: memberOf } = useResourceCollections(urn);
  const toggle = useToggleCollectionMember();
  const memberIds = new Set(memberOf.map((c) => c.id));

  return (
    <>
      <DialogBody>
        {allCollections.length === 0 && (
          <p className="hl-text-muted">No collections yet — create one from the Collections page first.</p>
        )}
        {allCollections.map((c) => (
          <Checkbox
            key={c.id}
            label={c.name}
            checked={memberIds.has(c.id)}
            disabled={toggle.isPending}
            onChange={(e) => toggle.mutate({ collectionId: c.id, urn, member: e.target.checked })}
          />
        ))}
      </DialogBody>
      <DialogFooter actions={<Button onClick={onClose}>Done</Button>} />
    </>
  );
}

export function ResourceTagBadges({ urn }: { urn: string }) {
  const { data } = useResourceTags();
  const current = data?.find((r) => r.resource_urn === urn);
  if (!current || (current.tags.length === 0 && !current.featured)) return null;

  return (
    <>
      {current.featured && (
        <Tag minimal icon="star" intent="warning">
          Promoted
        </Tag>
      )}
      {current.tags.map((tag) => (
        <Tag key={tag} minimal icon="tag">
          {tag}
        </Tag>
      ))}
    </>
  );
}
