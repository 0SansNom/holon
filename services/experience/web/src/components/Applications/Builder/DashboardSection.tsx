import { useDroppable } from "@dnd-kit/core";
import { SortableContext, useSortable, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Button, Card, Checkbox, HTMLSelect, InputGroup, Icon } from "@blueprintjs/core";
import type { ObjectSet, ObjectType } from "../../../api/knowledge";
import { urnShortName } from "../../ObjectExplorer/objectExplorerUtils";

export interface DashboardWidgetConfig {
  id: string;
  component: "kpi" | "table";
  objectType: string;
  objectSet: string;
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
  objectSets,
  onChange,
  onRemove,
}: {
  widget: DashboardWidgetConfig;
  objectTypes: ObjectType[];
  objectSets: ObjectSet[];
  onChange: (widget: DashboardWidgetConfig) => void;
  onRemove: () => void;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id: widget.id });
  const setsForType = objectSets.filter(
    (os) => os.visibility !== "hidden" && (!widget.objectType || urnShortName(os.object_type_urn) === widget.objectType),
  );

  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition, opacity: isDragging ? 0.4 : 1 }}
    >
      <Card className="hl-builder-widget-card">
        <span {...listeners} {...attributes} className="hl-drag-handle">
          <Icon icon="drag-handle-vertical" />
        </span>
        <Icon icon={widget.component === "kpi" ? "numerical" : "th"} />
        <HTMLSelect
          value={widget.objectType}
          onChange={(e) =>
            onChange({
              ...widget,
              objectType: e.target.value,
              objectSet: "",
            })
          }
          minimal
        >
          <option value="">Select ObjectType…</option>
          {objectTypes.map((ot) => (
            <option key={ot.name} value={ot.name}>
              {ot.name}
            </option>
          ))}
        </HTMLSelect>
        <HTMLSelect
          value={widget.objectSet}
          disabled={!widget.objectType}
          onChange={(e) => onChange({ ...widget, objectSet: e.target.value })}
          minimal
          title="Optional Object Set filter"
        >
          <option value="">All instances</option>
          {setsForType.map((os) => (
            <option key={os.name} value={os.name}>
              {os.display_name || os.name}
            </option>
          ))}
        </HTMLSelect>
        <InputGroup
          placeholder="Label"
          value={widget.label}
          onChange={(e) => onChange({ ...widget, label: e.target.value })}
          className="hl-flex-1"
        />
        <Button icon="cross" minimal small onClick={onRemove} title="Remove widget" />
      </Card>
      {!widget.objectType && (
        <p className="hl-builder-widget-warning">
          Select an ObjectType above, or this widget will be dropped when you save.
        </p>
      )}
    </div>
  );
}

export function DashboardSection({
  value,
  objectTypes,
  objectSets,
  onChange,
}: {
  value: DashboardValue;
  objectTypes: ObjectType[];
  objectSets: ObjectSet[];
  onChange: (value: DashboardValue) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: "dashboard-canvas" });
  const widgetIds = value.widgets.map((w) => w.id);

  return (
    <Card>
      <div className={`hl-builder-section-header${value.enabled ? " hl-builder-section-header--expanded" : ""}`}>
        <Checkbox
          checked={value.enabled}
          label="Dashboard"
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
          className="hl-builder-checkbox"
        />
        {value.enabled && (
          <InputGroup
            small
            placeholder="/apps/name/dashboard"
            value={value.route}
            onChange={(e) => onChange({ ...value, route: e.target.value })}
            className="hl-builder-route-input"
          />
        )}
      </div>

      {value.enabled && (
        <div ref={setNodeRef} className="hl-builder-canvas" data-over={isOver}>
          {value.widgets.length === 0 && (
            <p className="hl-ontology-tab-desc">Drag a KPI or Table widget from the palette here.</p>
          )}
          <SortableContext items={widgetIds} strategy={verticalListSortingStrategy}>
            {value.widgets.map((widget) => (
              <SortableWidgetRow
                key={widget.id}
                widget={widget}
                objectTypes={objectTypes}
                objectSets={objectSets}
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
