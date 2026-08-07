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
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: value.enabled ? 12 : 0 }}>
        <Checkbox
          checked={value.enabled}
          label="Object App"
          onChange={(e) => onChange({ ...value, enabled: e.target.checked })}
          style={{ marginBottom: 0, fontWeight: 600 }}
        />
        {value.enabled && (
          <InputGroup
            small
            placeholder="/apps/name"
            value={value.route}
            onChange={(e) => onChange({ ...value, route: e.target.value })}
            style={{ maxWidth: 260 }}
          />
        )}
      </div>

      {value.enabled && (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <label style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
            ObjectType
            <HTMLSelect
              fill
              value={value.objectType}
              onChange={(e) => onChange({ ...value, objectType: e.target.value, actions: [] })}
              style={{ marginTop: 4 }}
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
              <div style={{ fontSize: 12, color: "var(--hl-text-muted)", marginBottom: 6 }}>Actions to expose</div>
              {availableActions.length === 0 && (
                <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>No Actions declared for this ObjectType.</p>
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
                        <Tag minimal intent={action.risk_level === "high" ? "danger" : "none"} style={{ marginLeft: 4 }}>
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
