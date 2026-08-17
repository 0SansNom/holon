/**
 * Heuristic for ontology / app resources left behind by pytest helpers.
 * Demo seed names (Customer, Order, …) never match.
 *
 * Patterns covered:
 * - conftest `_unique_name`: `Prefix_1739123456789` (ms timestamp)
 * - interfaces-style: `CanRelax214e0233` (CamelCase + 8 hex)
 * - app-builder: `ShippedOrdersAppa1b2c3` (CamelCase + 6–8 hex)
 * - uuid-suffix: `thing-a1b2c3d4`
 * - application / pipeline: any `test-…` name
 * - dotted Action Types: `OsdkReview_….setPriority`
 */

const MS_TIMESTAMP_SUFFIX = /_\d{10,}$/;
const DASH_HEX_SUFFIX = /-[0-9a-f]{6,10}$/i;
const CAMEL_HEX_SUFFIX = /^[A-Z][A-Za-z0-9]*[0-9a-f]{6,8}$/;
const LOWER_HEX_SUFFIX = /^[a-z][a-z0-9_]*[0-9a-f]{6,8}$/;
const TEST_PREFIX = /^test-/i;

function matchesEphemeralLeaf(name: string): boolean {
  if (TEST_PREFIX.test(name)) return true;
  if (MS_TIMESTAMP_SUFFIX.test(name)) return true;
  if (DASH_HEX_SUFFIX.test(name)) return true;
  if (CAMEL_HEX_SUFFIX.test(name)) return true;
  if (LOWER_HEX_SUFFIX.test(name)) return true;
  return false;
}

export function isEphemeralTestName(name: string | null | undefined): boolean {
  if (!name) return false;
  const trimmed = name.trim();
  if (!trimmed) return false;
  if (trimmed.includes(".")) {
    return trimmed.split(".").some((part) => part.length > 0 && matchesEphemeralLeaf(part));
  }
  return matchesEphemeralLeaf(trimmed);
}

export function partitionEphemeral<T>(
  items: T[],
  nameOf: (item: T) => string,
): { kept: T[]; hidden: T[] } {
  const kept: T[] = [];
  const hidden: T[] = [];
  for (const item of items) {
    if (isEphemeralTestName(nameOf(item))) hidden.push(item);
    else kept.push(item);
  }
  return { kept, hidden };
}
