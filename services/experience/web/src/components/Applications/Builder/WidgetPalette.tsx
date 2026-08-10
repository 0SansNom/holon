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
      <Card className="hl-palette-chip">
        <Icon icon={icon} size={14} />
        <span className="hl-palette-chip-label">{label}</span>
      </Card>
    </div>
  );
}

export function WidgetPalette() {
  return (
    <div>
      <div className="hl-section-title hl-mb-sm">Drag onto Dashboard</div>
      <PaletteChip id="palette-kpi" kind="kpi" label="KPI widget" icon="numerical" />
      <PaletteChip id="palette-table" kind="table" label="Table widget" icon="th" />
    </div>
  );
}
