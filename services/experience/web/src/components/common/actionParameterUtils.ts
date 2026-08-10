const INT_LIKE = new Set(["integer", "short", "byte", "long"]);
const FLOAT_LIKE = new Set(["double", "decimal", "float"]);

export function coerce(raw: string, baseType: string | undefined): unknown {
  if (baseType && (INT_LIKE.has(baseType) || FLOAT_LIKE.has(baseType))) {
    const n = Number(raw);
    if (Number.isNaN(n)) return raw;
    return INT_LIKE.has(baseType) ? Math.trunc(n) : n;
  }
  return raw;
}
