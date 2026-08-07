import { useDraggable } from "@dnd-kit/core";
import { Card, Icon, type IconName } from "@blueprintjs/core";

function PaletteChip({ id, kind, label, icon }: { id: string; kind: "kpi" | "table"; label: string; icon: IconName }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({ id, data: { kind } });
  return (
    <div
      ref={setNodeRef}
      {...listeners}
      {...attributes}
      style={{
        transform: transform ? `translate3d(${transform.x}px, ${transform.y}px, 0)` : undefined,
        opacity: isDragging ? 0.4 : 1,
        touchAction: "none",
      }}
    >
      <Card style={{ padding: "8px 12px", marginBottom: 8, cursor: "grab", display: "flex", alignItems: "center", gap: 8 }}>
        <Icon icon={icon} size={14} />
        <span style={{ fontSize: 12 }}>{label}</span>
      </Card>
    </div>
  );
}

// The one genuinely drag-and-drop part of the Builder (`@dnd-kit`,
// installed since the project's start but unused until Phase G): these
// two chips are draggable onto the dashboard canvas below, and the
// widgets they produce there are themselves sortable. Object App/Agent
// App get plain toggle+form sections instead, deliberately — a
// definition can only meaningfully carry *one* of each (Knowledge/
// Experience both resolve the *first* matching surface), so there's
// nothing to drag-and-drop reorder there; dashboard widgets are the
// one genuinely multi-item, reorderable case in this schema.
export function WidgetPalette() {
  return (
    <div>
      <div
        style={{
          fontSize: 11,
          textTransform: "uppercase",
          letterSpacing: "0.03em",
          color: "var(--hl-text-muted)",
          marginBottom: 8,
        }}
      >
        Drag onto Dashboard
      </div>
      <PaletteChip id="palette-kpi" kind="kpi" label="KPI widget" icon="numerical" />
      <PaletteChip id="palette-table" kind="table" label="Table widget" icon="th" />
    </div>
  );
}
