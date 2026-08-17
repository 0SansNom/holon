import { useEffect, useMemo, useState } from "react";
import { DndContext, type DragEndEvent, closestCenter } from "@dnd-kit/core";
import { SortableContext, arrayMove, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button, Checkbox, FormGroup, HTMLSelect, Icon } from "@blueprintjs/core";
import { RegistryDialog } from "../common/RegistryDialog";
import {
  normalizeColumnLayout,
  resolveVisibleColumnOrder,
  type ObjectTableColumnLayout,
} from "./columnLayout";

function SortableColumnRow({
  id,
  hidden,
  frozen,
  onToggleHidden,
}: {
  id: string;
  hidden: boolean;
  frozen: boolean;
  onToggleHidden: (hide: boolean) => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id });

  return (
    <div
      ref={setNodeRef}
      className="hl-oe-column-row"
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.6 : 1 }}
    >
      <span {...listeners} {...attributes} className="hl-drag-handle" title="Drag to reorder">
        <Icon icon="drag-handle-vertical" />
      </span>
      <Checkbox
        checked={!hidden}
        label={id}
        className="hl-oe-column-row-check"
        onChange={(e) => onToggleHidden(!(e.target as HTMLInputElement).checked)}
      />
      {frozen && !hidden && (
        <Icon icon="pin" size={12} className="hl-text-muted" title="Frozen" />
      )}
    </div>
  );
}

export function ColumnLayoutDialog({
  isOpen,
  objectTypeName,
  availableKeys,
  layout,
  onClose,
  onSave,
  onReset,
}: {
  isOpen: boolean;
  objectTypeName: string;
  availableKeys: string[];
  layout: ObjectTableColumnLayout;
  onClose: () => void;
  onSave: (layout: ObjectTableColumnLayout) => void;
  onReset: () => void;
}) {
  const [draft, setDraft] = useState(() => normalizeColumnLayout(layout));

  useEffect(() => {
    if (isOpen) setDraft(normalizeColumnLayout(layout));
  }, [isOpen, layout]);

  const { allOrdered, visibleOrder } = useMemo(
    () => resolveVisibleColumnOrder(availableKeys, draft),
    [availableKeys, draft],
  );
  const hiddenSet = useMemo(() => new Set(draft.hidden), [draft.hidden]);
  const frozenIds = useMemo(
    () => new Set(visibleOrder.slice(0, draft.freezeCount)),
    [visibleOrder, draft.freezeCount],
  );

  function onDragEnd(event: DragEndEvent) {
    const { active, over } = event;
    if (!over || active.id === over.id) return;
    const oldIndex = allOrdered.indexOf(String(active.id));
    const newIndex = allOrdered.indexOf(String(over.id));
    if (oldIndex < 0 || newIndex < 0) return;
    setDraft((prev) => ({ ...prev, order: arrayMove(allOrdered, oldIndex, newIndex) }));
  }

  return (
    <RegistryDialog
      isOpen={isOpen}
      title={`Columns · ${objectTypeName}`}
      onClose={onClose}
      error={null}
      isPending={false}
      submitLabel="Save layout"
      onSubmit={() => {
        onSave(normalizeColumnLayout(draft));
        onClose();
      }}
      footerStart={
        <Button
          minimal
          onClick={() => {
            onReset();
            onClose();
          }}
        >
          Reset to default
        </Button>
      }
    >
      <FormGroup
        label="Freeze leading columns"
        helperText="Keeps select + N first visible columns sticky while scrolling."
      >
        <HTMLSelect
          value={draft.freezeCount}
          onChange={(e) => setDraft((prev) => ({ ...prev, freezeCount: Number(e.target.value) }))}
        >
          {[0, 1, 2, 3, 4].map((n) => (
            <option key={n} value={n}>
              {n === 0 ? "None" : `${n} column${n === 1 ? "" : "s"}`}
            </option>
          ))}
        </HTMLSelect>
      </FormGroup>

      <FormGroup label="Visible columns (drag to reorder)">
        {allOrdered.length === 0 ? (
          <p className="hl-text-muted-sm">No columns available yet.</p>
        ) : (
          <DndContext collisionDetection={closestCenter} onDragEnd={onDragEnd}>
            <SortableContext items={allOrdered} strategy={verticalListSortingStrategy}>
              <div className="hl-oe-column-list">
                {allOrdered.map((id) => (
                  <SortableColumnRow
                    key={id}
                    id={id}
                    hidden={hiddenSet.has(id)}
                    frozen={frozenIds.has(id)}
                    onToggleHidden={(hide) =>
                      setDraft((prev) => {
                        const next = new Set(prev.hidden);
                        if (hide) next.add(id);
                        else next.delete(id);
                        return { ...prev, hidden: [...next] };
                      })
                    }
                  />
                ))}
              </div>
            </SortableContext>
          </DndContext>
        )}
      </FormGroup>
    </RegistryDialog>
  );
}
