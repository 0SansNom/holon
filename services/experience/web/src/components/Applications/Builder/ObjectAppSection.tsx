import { Card, Checkbox, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import type { ObjectType, ActionDefinition } from "../../../api/knowledge";

export interface ObjectAppValue {
  enabled: boolean;
  objectType: string;
  route: string;
  actions: string[]; // full "ObjectType.actionName" names
}

export function ObjectAppSection({
  value,
  objectTypes,
  actions,
  onChange,
}: {
  value: ObjectAppValue;
  objectTypes: ObjectType[];
  actions: ActionDefinition[];
  onChange: (value: ObjectAppValue) => void;
}) {
  const availableActions = actions.filter((a) => a.target_object_type === value.objectType);

  function toggleAction(name: string, checked: boolean) {
    onChange({
      ...value,
      actions: checked ? [...value.actions, name] : value.actions.filter((a) => a !== name),
    });
  }

  return (
    <Card>
      <div className={`hl-builder-section-header${value.enabled ? " hl-builder-section-header--expanded" : ""}`}>
        <Checkbox
          checked={value.enabled}
          label="Object App"
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
          className="hl-builder-checkbox"
        />
        {value.enabled && (
          <InputGroup
            small
            placeholder="/apps/name"
            value={value.route}
            onChange={(e) => onChange({ ...value, route: e.target.value })}
            className="hl-builder-route-input"
          />
        )}
      </div>

      {value.enabled && (
        <div className="hl-builder-fields">
          <label className="hl-text-muted">
            ObjectType
            <HTMLSelect
              fill
              value={value.objectType}
              onChange={(e) => onChange({ ...value, objectType: e.target.value, actions: [] })}
              className="hl-builder-field-mt"
            >
              <option value="">Select ObjectType…</option>
              {objectTypes.map((ot) => (
                <option key={ot.name} value={ot.name}>
                  {ot.name}
                </option>
              ))}
            </HTMLSelect>
          </label>

          {value.objectType && (
            <div>
              <div className="hl-section-title hl-mb-sm">Actions to expose</div>
              {availableActions.length === 0 && (
                <p className="hl-text-muted">No Actions declared for this ObjectType.</p>
              )}
              {availableActions.map((action) => {
                const localName = action.name.split(".", 2)[1];
                return (
                  <Checkbox
                    key={action.name}
                    checked={value.actions.includes(action.name)}
                    onChange={(e) => toggleAction(action.name, e.target.checked)}
                    labelElement={
                      <span>
                        {localName}{" "}
                        <Tag minimal intent={action.risk_level === "high" ? "danger" : "none"} className="hl-ml-xs">
                          {action.risk_level}
                        </Tag>
                      </span>
                    }
                  />
                );
              })}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
