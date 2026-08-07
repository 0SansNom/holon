import { useDroppable } from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button, Card, Checkbox, HTMLSelect, InputGroup, Icon } from "@blueprintjs/core";
import type { ObjectType } from "../../../api/knowledge";

export interface DashboardWidgetConfig {
  id: string;
  component: "kpi" | "table";
  objectType: string;
  label: string;
}

export interface DashboardValue {
  enabled: boolean;
  route: string;
  widgets: DashboardWidgetConfig[];
}

function SortableWidgetRow({
  widget,
  objectTypes,
  onChange,
  onRemove,
}: {
  widget: DashboardWidgetConfig;
  objectTypes: ObjectType[];
  onChange: (widget: DashboardWidgetConfig) => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: widget.id });
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.4 : 1 }}
    >
      <Card style={{ display: "flex", alignItems: "center", gap: 8, padding: 8, marginBottom: 6 }}>
        <span {...listeners} {...attributes} style={{ cursor: "grab", touchAction: "none" }}>
          <Icon icon="drag-handle-vertical" />
        </span>
        <Icon icon={widget.component === "kpi" ? "numerical" : "th"} />
        <HTMLSelect
          value={widget.objectType}
          onChange={(e) => onChange({ ...widget, objectType: e.target.value })}
          minimal
        >
          <option value="">Select ObjectType…</option>
          {objectTypes.map((ot) => (
            <option key={ot.name} value={ot.name}>
              {ot.name}
            </option>
          ))}
        </HTMLSelect>
        <InputGroup
          placeholder="Label"
          value={widget.label}
          onChange={(e) => onChange({ ...widget, label: e.target.value })}
          style={{ flex: 1 }}
        />
        <Button icon="cross" minimal small onClick={onRemove} title="Remove widget" />
      </Card>
      {!widget.objectType && (
        <p style={{ fontSize: 11, color: "var(--hl-warning)", margin: "-4px 0 6px 32px" }}>
          Select an ObjectType above, or this widget will be dropped when you save.
        </p>
      )}
    </div>
  );
}

export function DashboardSection({
  value,
  objectTypes,
  onChange,
}: {
  value: DashboardValue;
  objectTypes: ObjectType[];
  onChange: (value: DashboardValue) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: "dashboard-canvas" });
  const widgetIds = value.widgets.map((w) => w.id);

  return (
    <Card>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: value.enabled ? 12 : 0 }}>
        <Checkbox
          checked={value.enabled}
          label="Dashboard"
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
          style={{ marginBottom: 0, fontWeight: 600 }}
        />
        {value.enabled && (
          <InputGroup
            small
            placeholder="/apps/name/dashboard"
            value={value.route}
            onChange={(e) => onChange({ ...value, route: e.target.value })}
            style={{ maxWidth: 260 }}
          />
        )}
      </div>

      {value.enabled && (
        <div
          ref={setNodeRef}
          style={{
            border: `2px dashed ${isOver ? "var(--hl-accent)" : "var(--hl-border)"}`,
            borderRadius: 4,
            padding: 12,
            minHeight: 80,
            background: isOver ? "rgba(79, 140, 255, 0.06)" : "transparent",
          }}
        >
          {value.widgets.length === 0 && (
            <p style={{ fontSize: 12, color: "var(--hl-text-muted)", margin: 0 }}>
              Drag a KPI or Table widget from the palette here.
            </p>
          )}
          <SortableContext items={widgetIds} strategy={verticalListSortingStrategy}>
            {value.widgets.map((widget) => (
              <SortableWidgetRow
                key={widget.id}
                widget={widget}
                objectTypes={objectTypes}
                onChange={(updated) =>
                  onChange({ ...value, widgets: value.widgets.map((w) => (w.id === updated.id ? updated : w)) })
                }
                onRemove={() => onChange({ ...value, widgets: value.widgets.filter((w) => w.id !== widget.id) })}
              />
            ))}
          </SortableContext>
        </div>
      )}
    </Card>
  );
}
