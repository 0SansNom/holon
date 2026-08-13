import { Button, Checkbox, FormGroup, HTMLSelect, InputGroup } from "@blueprintjs/core";
import type { InterfaceLinkConstraint } from "../../api/knowledge";

export function InterfaceLinkConstraintsFields({
  constraints,
  onChange,
  objectTypeNames,
  interfaceNames,
}: {
  constraints: InterfaceLinkConstraint[];
  onChange: (next: InterfaceLinkConstraint[]) => void;
  objectTypeNames: string[];
  interfaceNames: string[];
}) {
  function updateAt(index: number, patch: Partial<InterfaceLinkConstraint>) {
    onChange(constraints.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  }

  function addConstraint() {
    onChange([
      ...constraints,
      {
        api_name: "",
        target_kind: "object_type",
        target: objectTypeNames[0] ?? "",
        cardinality: "one",
        required: true,
        description: "",
      },
    ]);
  }

  return (
    <div>
      <p className="hl-text-muted-sm" style={{ marginBottom: 8 }}>
        Abstract links fulfilled by a concrete RelationType when an ObjectType implements this
        interface.
      </p>
      {constraints.map((constraint, index) => {
        const targets = constraint.target_kind === "object_type" ? objectTypeNames : interfaceNames;
        return (
          <div key={index} className="hl-mt-xs" style={{ borderTop: "1px solid var(--hl-border)", paddingTop: 8 }}>
            <div className="hl-flex-between">
              <strong className="hl-text-muted-sm">Constraint {index + 1}</strong>
              <Button
                minimal
                small
                icon="trash"
                intent="danger"
                onClick={() => onChange(constraints.filter((_, i) => i !== index))}
              />
            </div>
            <FormGroup label="API name">
              <InputGroup
                className="hl-mono"
                placeholder="customer"
                value={constraint.api_name}
                onChange={(e) => updateAt(index, { api_name: e.target.value })}
              />
            </FormGroup>
            <div className="hl-flex-row" style={{ gap: 8 }}>
              <FormGroup label="Target kind" style={{ flex: 1 }}>
                <HTMLSelect
                  fill
                  value={constraint.target_kind}
                  onChange={(e) => {
                    const target_kind = e.target.value as "object_type" | "interface";
                    const nextTargets = target_kind === "object_type" ? objectTypeNames : interfaceNames;
                    updateAt(index, { target_kind, target: nextTargets[0] ?? "" });
                  }}
                >
                  <option value="object_type">Object type</option>
                  <option value="interface">Interface</option>
                </HTMLSelect>
              </FormGroup>
              <FormGroup label="Target" style={{ flex: 1 }}>
                <HTMLSelect
                  fill
                  value={constraint.target}
                  onChange={(e) => updateAt(index, { target: e.target.value })}
                >
                  {targets.length === 0 && <option value="">None available</option>}
                  {targets.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </HTMLSelect>
              </FormGroup>
            </div>
            <div className="hl-flex-row" style={{ gap: 8, alignItems: "center" }}>
              <FormGroup label="Cardinality" style={{ flex: 1 }}>
                <HTMLSelect
                  fill
                  value={constraint.cardinality}
                  onChange={(e) =>
                    updateAt(index, { cardinality: e.target.value as "one" | "many" })
                  }
                >
                  <option value="one">one</option>
                  <option value="many">many</option>
                </HTMLSelect>
              </FormGroup>
              <Checkbox
                checked={constraint.required}
                label="Required"
                onChange={(e) => updateAt(index, { required: e.currentTarget.checked })}
              />
            </div>
            <FormGroup label="Description">
              <InputGroup
                value={constraint.description ?? ""}
                onChange={(e) => updateAt(index, { description: e.target.value })}
              />
            </FormGroup>
          </div>
        );
      })}
      <Button icon="add" minimal className="hl-mt-xs" onClick={addConstraint}>
        Add link constraint
      </Button>
    </div>
  );
}
