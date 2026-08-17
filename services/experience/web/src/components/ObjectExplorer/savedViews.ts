import type { ObjectTableColumnLayout } from "./columnLayout";
import { normalizeColumnLayout } from "./columnLayout";
import type { PredicateFormRow } from "../Ontology/objectSetPredicates";

/** Saved OE exploration — dynamic filters + column layout (not an Object Set). */
export type SavedExploration = {
  id: string;
  name: string;
  objectType: string;
  objectSet?: string;
  filters: PredicateFormRow[];
  columnLayout: ObjectTableColumnLayout;
  chartCollapsed?: boolean;
  globalFilter?: string;
  updatedAt: string;
};

/** Saved OE list — static instance IDs (Foundry-style list). */
export type SavedObjectList = {
  id: string;
  name: string;
  objectType: string;
  instanceIds: string[];
  updatedAt: string;
};

export function newViewId(): string {
  return crypto.randomUUID();
}

export function normalizeExploration(raw: Partial<SavedExploration> | null | undefined): SavedExploration | null {
  if (!raw?.id || !raw.name || !raw.objectType) return null;
  const filters = Array.isArray(raw.filters)
    ? raw.filters
        .filter((f) => f && typeof f.property === "string")
        .map((f) => ({
          property: String(f.property),
          op: String(f.op || "eq"),
          value: String(f.value ?? ""),
        }))
    : [];
  return {
    id: String(raw.id),
    name: String(raw.name).trim() || "Untitled exploration",
    objectType: String(raw.objectType),
    objectSet: raw.objectSet ? String(raw.objectSet) : undefined,
    filters,
    columnLayout: normalizeColumnLayout(raw.columnLayout),
    chartCollapsed: Boolean(raw.chartCollapsed),
    globalFilter: raw.globalFilter ? String(raw.globalFilter) : undefined,
    updatedAt: raw.updatedAt ? String(raw.updatedAt) : new Date().toISOString(),
  };
}

export function normalizeObjectList(raw: Partial<SavedObjectList> | null | undefined): SavedObjectList | null {
  if (!raw?.id || !raw.name || !raw.objectType) return null;
  const instanceIds = Array.isArray(raw.instanceIds)
    ? [...new Set(raw.instanceIds.map((id) => String(id)).filter(Boolean))]
    : [];
  return {
    id: String(raw.id),
    name: String(raw.name).trim() || "Untitled list",
    objectType: String(raw.objectType),
    instanceIds,
    updatedAt: raw.updatedAt ? String(raw.updatedAt) : new Date().toISOString(),
  };
}

export function filterRowsByListIds(
  rows: Record<string, unknown>[],
  instanceIds: string[],
): Record<string, unknown>[] {
  if (instanceIds.length === 0) return [];
  const want = new Set(instanceIds);
  return rows.filter((r) => want.has(String(r.id)));
}
