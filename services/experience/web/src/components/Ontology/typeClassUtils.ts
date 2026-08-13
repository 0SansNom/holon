/** Foundry Type class helpers — `kind:name` encoding + known catalog keys. */

const TYPE_CLASS_RE =
  /^(?:[a-z][a-z0-9_-]{0,63}|[a-z][a-z0-9_-]{0,63}:[A-Za-z0-9_.:-]{1,128})$/;

export function parseTypeClassesInput(raw: string): string[] {
  return raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

export function hasTypeClass(typeClasses: string[] | undefined | null, kind: string, name: string): boolean {
  const target = `${kind}:${name}`;
  return (typeClasses ?? []).some((c) => c === target);
}

export function isValidTypeClass(raw: string): boolean {
  return TYPE_CLASS_RE.test(raw.trim());
}

export function findPropertyWithTypeClass(
  propertyTypes: Record<string, { type_classes?: string[] }> | undefined | null,
  kind: string,
  name: string,
): string | null {
  if (!propertyTypes) return null;
  for (const [prop, rule] of Object.entries(propertyTypes)) {
    if (hasTypeClass(rule?.type_classes, kind, name)) return prop;
  }
  return null;
}
