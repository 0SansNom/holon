import type { ConditionalFormatRule, ObjectType, SharedPropertyType } from "../../api/knowledge";
import { camelToSnake } from "../common/propertyFormatUtils";
import { expandFilterPropertyKeys } from "../Ontology/objectSetPredicates";
import {
  isPropertyHidden,
  sortPropertiesByVisibility,
} from "../Ontology/propertyEditorUtils";
import { isEphemeralTestName } from "../Ontology/ephemeralResources";
import { filterRowsByListIds } from "./savedViews";
import {
  OBJECT_METADATA_KEYS,
  buildExplorerColumnKeys,
  preferTitleColumnFirst,
  resolveInstanceColumnKey,
  urnShortName,
} from "./objectExplorerUtils";

export const BATCH_ACTION_CAP = 50;
export const FROZEN_SELECT_WIDTH = 36;
export const FROZEN_DATA_COL_WIDTH = 140;

export function frozenLeftOffset(dataIndex: number): number {
  return FROZEN_SELECT_WIDTH + dataIndex * FROZEN_DATA_COL_WIDTH;
}

export function shouldUseServerPaging(opts: {
  setName?: string;
  listId?: string;
  predicateCount: number;
  globalFilter: string;
}): boolean {
  return !opts.setName && !opts.listId && opts.predicateCount === 0 && opts.globalFilter.trim() === "";
}

export function visibleObjectSetsForType<T extends { name: string; object_type_urn: string; visibility?: string }>(
  objectSets: T[],
  type: string,
  activeSetName?: string,
): T[] {
  return objectSets.filter((os) => {
    if (urnShortName(os.object_type_urn) !== type || os.visibility === "hidden") return false;
    if (activeSetName && os.name === activeSetName) return true;
    return !isEphemeralTestName(os.name);
  });
}

export function selectObjectTableBaseRows<T extends Record<string, unknown>>(opts: {
  activeList?: { instanceIds: string[] } | null;
  useServerPaging: boolean;
  setName?: string;
  setTypeMismatch: boolean;
  allRows?: T[] | null;
  serverPageData?: T[] | null;
  evaluatedData?: T[] | null;
}): T[] {
  if (opts.activeList) {
    return filterRowsByListIds(opts.allRows ?? [], opts.activeList.instanceIds) as T[];
  }
  if (opts.useServerPaging) return opts.serverPageData ?? [];
  if (!opts.setName) return opts.allRows ?? [];
  if (opts.setTypeMismatch) return [];
  return opts.evaluatedData ?? [];
}

export function explorerPropertyKeys(
  objectType: Pick<ObjectType, "property_mapping" | "property_types"> | null | undefined,
  baseRows: Record<string, unknown>[],
): string[] {
  const mapping = objectType?.property_mapping ?? {};
  const fromMapping = expandFilterPropertyKeys(mapping, objectType?.property_types);
  if (fromMapping.length > 0) return fromMapping;
  const first = baseRows[0];
  if (!first) return [];
  return Object.keys(first).filter((k) => !OBJECT_METADATA_KEYS.has(k));
}

export function explorerAvailableColumnKeys(
  objectType: Pick<ObjectType, "property_mapping" | "property_types" | "derived_properties" | "title_key"> | null | undefined,
  firstRow: Record<string, unknown> | null,
  sharedPropertyTypes: SharedPropertyType[],
): string[] {
  const ordered = buildExplorerColumnKeys(objectType, firstRow);
  const mapping = objectType?.property_mapping ?? {};
  const rowKeys = firstRow ? new Set(Object.keys(firstRow)) : null;
  const ontology = new Set(
    [...Object.keys(mapping), ...Object.keys(objectType?.derived_properties ?? {})].map((api) =>
      resolveInstanceColumnKey(api, mapping, rowKeys),
    ),
  );
  const visible = (keys: string[]) =>
    sortPropertiesByVisibility(
      keys,
      objectType?.property_types,
      objectType?.property_mapping,
      sharedPropertyTypes,
    ).filter(
      (k) =>
        !isPropertyHidden(k, objectType?.property_types, objectType?.property_mapping, sharedPropertyTypes),
    );
  if (ontology.size === 0) {
    return preferTitleColumnFirst(visible(ordered), objectType);
  }
  const ontologyKeys = preferTitleColumnFirst(
    visible(ordered.filter((k) => ontology.has(k))),
    objectType,
  );
  const extraKeys = visible(ordered.filter((k) => !ontology.has(k)));
  return [...ontologyKeys, ...extraKeys];
}

export function conditionalFormatsBySourceKey(
  conditionalFormats?: Record<string, ConditionalFormatRule[]> | null,
): Map<string, ConditionalFormatRule[]> {
  const map = new Map<string, ConditionalFormatRule[]>();
  for (const [property, rules] of Object.entries(conditionalFormats ?? {})) {
    map.set(camelToSnake(property), rules);
  }
  return map;
}

/** Knowledge invoke accepts the local Action name when the definition carries parameters. */
export function resolveInvokeActionName(
  action: { name: string; parameters?: unknown } | undefined,
  activeActionName: string,
): string {
  return action?.parameters !== undefined ? activeActionName : activeActionName.split(".")[1] ?? activeActionName;
}

export function bulkActionTargets(
  selectedIds: string[],
  focusedId: string | null,
  cap = BATCH_ACTION_CAP,
): { ids: string[]; capWarning: string | null } {
  if (selectedIds.length > 0) {
    return {
      ids: selectedIds.slice(0, cap),
      capWarning:
        selectedIds.length > cap
          ? `Selection has ${selectedIds.length} objects; only the first ${cap} will be included (API cap).`
          : null,
    };
  }
  return { ids: focusedId ? [focusedId] : [], capWarning: null };
}

export function nextFocusedRowId(
  rowSelection: Record<string, boolean>,
  focusedId: string | null,
): { focusedId: string | null; openPreview: boolean } {
  const selectedIds = Object.keys(rowSelection).filter((id) => rowSelection[id]);
  if (focusedId && !rowSelection[focusedId]) {
    return { focusedId: selectedIds[0] ?? null, openPreview: false };
  }
  if (!focusedId && selectedIds.length > 0) {
    return { focusedId: selectedIds[0], openPreview: true };
  }
  return { focusedId, openPreview: false };
}

export function formatSingleInvokeMessage(status?: string): string {
  return status === "pending_approval"
    ? "Submitted for approval (high-risk Action)."
    : "Applied immediately.";
}

export function formatBatchInvokeMessage(
  actionName: string,
  response: {
    succeeded: number;
    failed: number;
    count: number;
    results: Array<{ ok: boolean; result?: Record<string, unknown> }>;
  },
): { ok: boolean; message: string } {
  const pending = response.results.filter(
    (r) => r.ok && r.result?.status === "pending_approval",
  ).length;
  const parts = [
    `${response.succeeded} succeeded`,
    response.failed > 0 ? `${response.failed} failed` : null,
    pending > 0 ? `${pending} pending approval` : null,
  ].filter(Boolean);
  return {
    ok: response.failed === 0,
    message: `Bulk ${actionName}: ${parts.join(", ")} (of ${response.count}).`,
  };
}
