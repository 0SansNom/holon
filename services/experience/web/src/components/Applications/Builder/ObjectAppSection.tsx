import { Card, Checkbox, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import type { ObjectSet, ObjectType, ActionDefinition, RelationType } from "../../../api/knowledge";
import { urnShortName } from "../../ObjectExplorer/objectExplorerUtils";

export interface ObjectAppValue {
  enabled: boolean;
  objectType: string;
  objectSet: string;
  route: string;
  actions: string[]; // full "ObjectType.actionName" names
  links: string[]; // RelationType accessor names
}

export function ObjectAppSection({
  value,
  objectTypes,
  objectSets,
  actions,
  relationTypes,
  onChange,
}: {
  value: ObjectAppValue;
  objectTypes: ObjectType[];
  objectSets: ObjectSet[];
  actions: ActionDefinition[];
  relationTypes: RelationType[];
  onChange: (value: ObjectAppValue) => void;
}) {
  const availableActions = actions.filter((a) => a.target_object_type === value.objectType);
  const setsForType = objectSets.filter(
    (os) => os.visibility !== "hidden" && (!value.objectType || urnShortName(os.object_type_urn) === value.objectType),
  );
  const availableLinks = relationTypes.flatMap((rt) => {
    const source = urnShortName(rt.source_object_type_urn);
    const target = urnShortName(rt.target_object_type_urn);
    const local = rt.name.split(".").at(-1) || rt.name;
    const fwd = rt.source_api_name || local;
    const rev = rt.target_api_name || rt.target_property;
    const out: string[] = [];
    if (source === value.objectType) out.push(fwd);
    if (target === value.objectType && rev) out.push(rev);
    return out;
  });

  function toggleAction(name: string, checked: boolean) {
    onChange({
      ...value,
      actions: checked ? [...value.actions, name] : value.actions.filter((a) => a !== name),
    });
  }

  function toggleLink(name: string, checked: boolean) {
    onChange({
      ...value,
      links: checked ? [...value.links, name] : value.links.filter((a) => a !== name),
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
              onChange={(e) => onChange({ ...value, objectType: e.target.value, objectSet: "", actions: [], links: [] })}
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
            <label className="hl-text-muted">
              Object Set (optional)
              <HTMLSelect
                fill
                value={value.objectSet}
                onChange={(e) => onChange({ ...value, objectSet: e.target.value })}
                className="hl-builder-field-mt"
              >
                <option value="">All {value.objectType} instances</option>
                {setsForType.map((os) => (
                  <option key={os.name} value={os.name}>
                    {os.display_name || os.name}
                  </option>
                ))}
              </HTMLSelect>
            </label>
          )}

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

          {value.objectType && (
            <div>
              <div className="hl-section-title hl-mb-sm">Related links to expose</div>
              {availableLinks.length === 0 && (
                <p className="hl-text-muted">No RelationTypes attached to this ObjectType.</p>
              )}
              {[...new Set(availableLinks)].map((linkName) => (
                <Checkbox
                  key={linkName}
                  checked={value.links.includes(linkName)}
                  onChange={(e) => toggleLink(linkName, e.target.checked)}
                  labelElement={<span className="hl-mono">{linkName}</span>}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
