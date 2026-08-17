/** Per–ObjectType column layout for Object Explorer (user preference, local). */

export type ObjectTableColumnLayout = {
  /** Preferred data-column order (ids). Unknown keys ignored; new keys append. */
  order: string[];
  /** User-hidden column ids (ontology-hidden stays out of the table entirely). */
  hidden: string[];
  /** Freeze select + this many leading visible data columns. */
  freezeCount: number;
};

export const DEFAULT_COLUMN_LAYOUT: ObjectTableColumnLayout = {
  order: [],
  hidden: [],
  freezeCount: 0,
};

export function normalizeColumnLayout(
  layout: Partial<ObjectTableColumnLayout> | null | undefined,
): ObjectTableColumnLayout {
  return {
    order: Array.isArray(layout?.order) ? layout.order.filter((k) => typeof k === "string") : [],
    hidden: Array.isArray(layout?.hidden) ? layout.hidden.filter((k) => typeof k === "string") : [],
    freezeCount: Math.max(0, Math.min(8, Number(layout?.freezeCount) || 0)),
  };
}

/**
 * Merge saved layout with columns currently available on the table.
 * Returns ordered visible ids + effective freeze count.
 */
export function resolveVisibleColumnOrder(
  availableKeys: string[],
  layout: ObjectTableColumnLayout | null | undefined,
): { visibleOrder: string[]; freezeCount: number; allOrdered: string[] } {
  const normalized = normalizeColumnLayout(layout);
  const available = new Set(availableKeys);
  const hidden = new Set(normalized.hidden.filter((k) => available.has(k)));
  const orderedPreferred = normalized.order.filter((k) => available.has(k));
  const rest = availableKeys.filter((k) => !orderedPreferred.includes(k));
  const allOrdered = [...orderedPreferred, ...rest];
  let visibleOrder = allOrdered.filter((k) => !hidden.has(k));
  if (visibleOrder.length === 0 && availableKeys.length > 0) {
    visibleOrder = [availableKeys[0]];
  }
  const freezeCount = Math.min(normalized.freezeCount, visibleOrder.length);
  return { visibleOrder, freezeCount, allOrdered };
}

export function toggleHidden(layout: ObjectTableColumnLayout, key: string, hide: boolean): ObjectTableColumnLayout {
  const hidden = new Set(layout.hidden);
  if (hide) hidden.add(key);
  else hidden.delete(key);
  return { ...layout, hidden: [...hidden] };
}
