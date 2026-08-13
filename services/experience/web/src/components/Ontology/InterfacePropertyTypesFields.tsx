import { FormGroup, HTMLSelect } from "@blueprintjs/core";
import type { InterfaceType } from "../../api/knowledge";

export type InterfacePropertyTypes = NonNullable<InterfaceType["property_types"]>;

/** Compact per-required-property VT/SPT binding editor for Interfaces. */
export function InterfacePropertyTypesFields({
  requiredProperties,
  propertyTypes,
  onChange,
  valueTypeNames,
  sharedPropertyTypeNames,
}: {
  requiredProperties: string[];
  propertyTypes: InterfacePropertyTypes;
  onChange: (next: InterfacePropertyTypes) => void;
  valueTypeNames: string[];
  sharedPropertyTypeNames: string[];
}) {
  if (requiredProperties.length === 0) {
    return (
      <p className="hl-text-muted-sm">
        Add required properties first to bind Shared Property Types or Value Types.
      </p>
    );
  }

  function setBinding(propertyName: string, mode: string, ref: string) {
    const next: InterfacePropertyTypes = { ...propertyTypes };
    if (mode === "" || !ref) {
      delete next[propertyName];
    } else if (mode === "value_type") {
      next[propertyName] = { kind: "value_type", value_type: ref };
    } else {
      next[propertyName] = { kind: "shared_property_type", shared_property_type: ref };
    }
    onChange(next);
  }

  return (
    <div className="hl-mt-xs">
      <p className="hl-text-muted-sm" style={{ marginBottom: 8 }}>
        Optional typed bindings — implementers must declare the same VT/SPT on that property at
        publish time.
      </p>
      {requiredProperties.map((propertyName) => {
        const rule = propertyTypes[propertyName];
        const mode = rule?.kind ?? "";
        const ref =
          rule?.kind === "value_type"
            ? rule.value_type
            : rule?.kind === "shared_property_type"
              ? rule.shared_property_type
              : "";
        return (
          <FormGroup key={propertyName} label={propertyName} labelInfo="(optional type)">
            <div className="hl-flex-row" style={{ gap: 8 }}>
              <HTMLSelect
                value={mode}
                onChange={(e) => {
                  const nextMode = e.target.value;
                  if (!nextMode) {
                    setBinding(propertyName, "", "");
                    return;
                  }
                  const names = nextMode === "value_type" ? valueTypeNames : sharedPropertyTypeNames;
                  setBinding(propertyName, nextMode, names[0] ?? "");
                }}
                style={{ minWidth: 160 }}
              >
                <option value="">Untyped (name only)</option>
                <option value="value_type">Value type</option>
                <option value="shared_property_type">Shared property type</option>
              </HTMLSelect>
              {mode === "value_type" && (
                <HTMLSelect
                  fill
                  value={ref}
                  onChange={(e) => setBinding(propertyName, "value_type", e.target.value)}
                >
                  {valueTypeNames.length === 0 && <option value="">No value types</option>}
                  {valueTypeNames.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </HTMLSelect>
              )}
              {mode === "shared_property_type" && (
                <HTMLSelect
                  fill
                  value={ref}
                  onChange={(e) => setBinding(propertyName, "shared_property_type", e.target.value)}
                >
                  {sharedPropertyTypeNames.length === 0 && (
                    <option value="">No shared property types</option>
                  )}
                  {sharedPropertyTypeNames.map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </HTMLSelect>
              )}
            </div>
          </FormGroup>
        );
      })}
    </div>
  );
}

export function prunePropertyTypes(
  requiredProperties: string[],
  propertyTypes: InterfacePropertyTypes,
): InterfacePropertyTypes {
  const allowed = new Set(requiredProperties);
  const next: InterfacePropertyTypes = {};
  for (const [key, rule] of Object.entries(propertyTypes)) {
    if (allowed.has(key)) next[key] = rule;
  }
  return next;
}

export function formatPropertyTypeBinding(
  rule: InterfacePropertyTypes[string] | undefined,
): string | null {
  if (!rule) return null;
  if (rule.kind === "value_type") return `VT:${rule.value_type}`;
  return `SPT:${rule.shared_property_type}`;
}
