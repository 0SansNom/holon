/** Object Set predicate ops — mirrors Knowledge `object_sets.VALID_OPS` / `matches_predicates`. */

import type { PropertyTypeRule } from "../../api/knowledge";

export const OBJECT_SET_OPS = ["eq", "neq", "in", "gt", "gte", "lt", "lte", "contains"] as const;

export type ObjectSetOp = (typeof OBJECT_SET_OPS)[number];

export type ObjectSetPredicate = { property: string; op: string; value: unknown };

export type PredicateFormRow = { property: string; op: string; value: string };

export function parsePredicateValue(op: string, raw: string): unknown {
  if (op === "in") {
    return raw
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean)
      .map((v) => {
        const n = Number(v);
        return Number.isFinite(n) && v !== "" ? n : v;
      });
  }
  const n = Number(raw);
  if (raw !== "" && Number.isFinite(n) && /^-?\d+(\.\d+)?$/.test(raw.trim())) return n;
  return raw;
}

export function predicateValueToInput(op: string, value: unknown): string {
  if (op === "in" && Array.isArray(value)) return value.map(String).join(", ");
  if (value == null) return "";
  return String(value);
}

export function buildPredicateDefinition(
  formPreds: PredicateFormRow[],
  options: { requireValue?: boolean } = {},
): { all: ObjectSetPredicate[] } {
  const requireValue = options.requireValue ?? true;
  const all = formPreds
    .filter((p) => p.property && (!requireValue || p.value.trim() !== ""))
    .map((p) => ({ property: p.property, op: p.op, value: parsePredicateValue(p.op, p.value) }));
  return { all };
}

/** Expand top-level properties with one-level struct field paths (`address.city`). */
export function expandFilterPropertyKeys(
  mapping: Record<string, string> | null | undefined,
  propertyTypes?: Record<string, PropertyTypeRule> | null,
): string[] {
  const keys = Object.keys(mapping ?? {});
  const out: string[] = [];
  for (const key of keys) {
    out.push(key);
    const rule = propertyTypes?.[key];
    if (rule?.kind === "struct" && rule.properties) {
      for (const field of Object.keys(rule.properties)) {
        out.push(`${key}.${field}`);
      }
    }
  }
  return out;
}

function resolvePredicateValue(
  instance: Record<string, unknown>,
  prop: string,
  propertyMapping: Record<string, string>,
): unknown {
  if (prop.includes(".")) {
    const [top, field, ...rest] = prop.split(".");
    if (!top || !field || rest.length > 0) return undefined;
    let container = instance[top];
    if (container == null) {
      const col = propertyMapping[top];
      if (col) container = instance[col];
    }
    if (container && typeof container === "object" && !Array.isArray(container)) {
      return (container as Record<string, unknown>)[field];
    }
    return undefined;
  }
  let actual: unknown = instance[prop];
  if (actual === undefined || actual === null) {
    const col = propertyMapping[prop];
    if (col) actual = instance[col];
  }
  return actual;
}

export function matchesPredicates(
  instance: Record<string, unknown>,
  definition: { all?: ObjectSetPredicate[] } | null | undefined,
  propertyMapping: Record<string, string> = {},
): boolean {
  for (const pred of definition?.all ?? []) {
    const prop = pred.property;
    const op = pred.op;
    const expected = pred.value;
    const actual = resolvePredicateValue(instance, prop, propertyMapping);
    if (op === "eq" && !(actual === expected)) return false;
    if (op === "neq" && !(actual !== expected)) return false;
    if (op === "in") {
      if (!Array.isArray(expected) || !expected.includes(actual as never)) return false;
    }
    if (op === "gt" && !(actual != null && (actual as number | string) > (expected as number | string))) {
      return false;
    }
    if (op === "gte" && !(actual != null && (actual as number | string) >= (expected as number | string))) {
      return false;
    }
    if (op === "lt" && !(actual != null && (actual as number | string) < (expected as number | string))) {
      return false;
    }
    if (op === "lte" && !(actual != null && (actual as number | string) <= (expected as number | string))) {
      return false;
    }
    if (op === "contains") {
      if (actual == null || !String(actual).includes(String(expected))) return false;
    }
  }
  return true;
}

export function formatPredicateChip(pred: ObjectSetPredicate): string {
  const value = Array.isArray(pred.value) ? pred.value.join(",") : String(pred.value);
  return `${pred.property} ${pred.op} ${value}`;
}
