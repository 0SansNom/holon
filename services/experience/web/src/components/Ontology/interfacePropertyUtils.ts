import type { InterfaceType } from "../../api/knowledge";

export type InterfacePropertyTypes = NonNullable<InterfaceType["property_types"]>;

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
