import type { ActionDefinition, ObjectType } from "../../api/knowledge";
import { camelToSnake } from "../common/propertyFormatUtils";

/** Materialisation metadata keys stripped from user-facing property lists. */
export const OBJECT_METADATA_KEYS = new Set([
  "materializedAt",
  "sourceLagSeconds",
  "degraded",
  "_maskedFields",
  "title",
]);

export function urnShortName(urn: string): string {
  const parts = urn.split(":");
  return parts[parts.length - 1] ?? urn;
}

/**
 * Search index docs use `{objectType}:{tenantId}:{instanceId}` as `urn`
 * (not a Holon resource URN). Parse that into Explorer route params.
 */
export function parseSearchHitRef(hit: {
  urn: string;
  object_type: string;
  tenant_id?: string;
}): { type: string; id: string } | null {
  const type = hit.object_type?.trim();
  if (!type || !hit.urn) return null;
  const prefix = `${type}:`;
  if (!hit.urn.startsWith(prefix)) return null;
  const rest = hit.urn.slice(prefix.length);
  if (hit.tenant_id && rest.startsWith(`${hit.tenant_id}:`)) {
    const id = rest.slice(hit.tenant_id.length + 1);
    return id ? { type, id } : null;
  }
  const colon = rest.indexOf(":");
  if (colon < 0) return null;
  const id = rest.slice(colon + 1);
  return id ? { type, id } : null;
}

/** Pick a property for a contains-predicate when saving a search as an Object Set. */
export function preferredSearchProperty(objectType?: {
  title_key?: string | null;
  primary_key?: string;
  property_mapping?: Record<string, string>;
} | null): string | null {
  if (!objectType?.property_mapping) return null;
  const keys = Object.keys(objectType.property_mapping);
  if (keys.length === 0) return null;
  for (const candidate of [objectType.title_key, "name", "title", objectType.primary_key, "id"]) {
    if (candidate && keys.includes(candidate)) return candidate;
  }
  return keys[0] ?? null;
}

export function objectSetBrowsePath(objectTypeName: string, objectSetName: string): {
  to: "/objects/$type";
  params: { type: string };
  search: { set: string };
} {
  return {
    to: "/objects/$type",
    params: { type: objectTypeName },
    search: { set: objectSetName },
  };
}

/** Display title for an instance — mirrors Knowledge `title_of`. */
export function titleOf(
  instance: Record<string, unknown> | null | undefined,
  objectType?: Pick<ObjectType, "title_key" | "primary_key" | "property_mapping"> | null,
): string {
  if (!instance) return "";
  const keys: string[] = [];
  if (objectType?.title_key) keys.push(objectType.title_key);
  if (objectType?.primary_key) keys.push(objectType.primary_key);
  keys.push("name", "id");
  const mapping = objectType?.property_mapping ?? {};
  for (const key of keys) {
    const direct = instance[key];
    if (direct != null && direct !== "") return String(direct);
    const col = mapping[key];
    if (col) {
      const viaCol = instance[col];
      if (viaCol != null && viaCol !== "") return String(viaCol);
      const snake = camelToSnake(col);
      const viaSnake = instance[snake];
      if (viaSnake != null && viaSnake !== "") return String(viaSnake);
    }
    const snakeKey = camelToSnake(key);
    const viaSnakeKey = instance[snakeKey];
    if (viaSnakeKey != null && viaSnakeKey !== "") return String(viaSnakeKey);
  }
  return String(instance.id ?? "");
}

/** One low-risk, single-edit inline action per property — shared by table and detail views. */
export function computeInlineEditableActions(
  type: string,
  implementedInterfaces: string[],
  allActions: ActionDefinition[],
): Map<string, ActionDefinition> {
  const relevant = allActions.filter(
    (a) => a.target_object_type === type || (a.target_interface && implementedInterfaces.includes(a.target_interface)),
  );
  const byProperty = new Map<string, ActionDefinition[]>();
  for (const action of relevant) {
    if (action.risk_level !== "low") continue;
    const edits = action.edits ?? [];
    const parameters = action.parameters ?? [];
    if (edits.length !== 1 || parameters.length !== 1) continue;
    const [edit] = edits;
    const [parameter] = parameters;
    if (edit.source !== "parameter" || edit.parameter_name !== parameter.name) continue;
    if (parameter.kind === "object_reference") continue;
    const list = byProperty.get(edit.property) ?? [];
    list.push(action);
    byProperty.set(edit.property, list);
  }
  const result = new Map<string, ActionDefinition>();
  for (const [property, candidates] of byProperty) {
    if (candidates.length === 1) result.set(camelToSnake(property), candidates[0]);
  }
  return result;
}

export interface RelatedLink {
  linkName: string;
  label: string;
  relatedType: string;
}
