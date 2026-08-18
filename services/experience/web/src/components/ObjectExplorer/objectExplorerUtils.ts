import type { ActionDefinition, ObjectType } from "../../api/knowledge";
import { isEphemeralTestName } from "../Ontology/ephemeralResources";
import { camelToSnake } from "../common/propertyFormatUtils";

/** Materialisation / OSDK wire keys stripped from user-facing property lists. */
export const OBJECT_METADATA_KEYS = new Set([
  "materializedAt",
  "sourceLagSeconds",
  "degraded",
  "_maskedFields",
  "title",
  "asOf",
  "__apiName",
  "__primaryKey",
  "__rid",
]);

/** Resolve a property/api name to the key present on a materialized instance row. */
export function resolveInstanceColumnKey(
  apiName: string,
  mapping?: Record<string, string> | null,
  rowKeys?: Set<string> | null,
): string {
  const col = mapping?.[apiName] ?? apiName;
  const candidates = [apiName, col, camelToSnake(apiName), camelToSnake(col)];
  if (rowKeys && rowKeys.size > 0) {
    const hit = candidates.find((c) => rowKeys.has(c));
    if (hit) return hit;
  }
  return col;
}

/** Read a property value from an instance using api name or backing column aliases. */
export function instancePropertyValue(
  instance: Record<string, unknown> | null | undefined,
  key: string,
  mapping?: Record<string, string> | null,
): unknown {
  if (!instance) return undefined;
  if (Object.prototype.hasOwnProperty.call(instance, key)) return instance[key];
  const col = mapping?.[key];
  if (col && Object.prototype.hasOwnProperty.call(instance, col)) return instance[col];
  if (col) {
    const snakeCol = camelToSnake(col);
    if (Object.prototype.hasOwnProperty.call(instance, snakeCol)) return instance[snakeCol];
  }
  const snakeKey = camelToSnake(key);
  if (Object.prototype.hasOwnProperty.call(instance, snakeKey)) return instance[snakeKey];
  return undefined;
}

function columnCoveredByOntology(
  key: string,
  ontologyCols: string[],
  mapping: Record<string, string>,
): boolean {
  if (ontologyCols.includes(key)) return true;
  const aliases = new Set<string>();
  for (const api of Object.keys(mapping)) {
    aliases.add(api);
    aliases.add(mapping[api]);
    aliases.add(camelToSnake(api));
    aliases.add(camelToSnake(mapping[api]));
  }
  for (const col of ontologyCols) {
    aliases.add(col);
    aliases.add(camelToSnake(col));
  }
  return aliases.has(key);
}

/**
 * Object Explorer table columns: ontology mapping + derived first (row-shaped
 * keys), then remaining instance fields (e.g. action side-effects). Avoids
 * alphabetical `account_closed` / booleans stealing the title slot.
 */
export function buildExplorerColumnKeys(
  objectType?: Pick<ObjectType, "property_mapping" | "derived_properties"> | null,
  row?: Record<string, unknown> | null,
): string[] {
  const mapping = objectType?.property_mapping ?? {};
  const derived = Object.keys(objectType?.derived_properties ?? {});
  const rowKeys = row ? new Set(Object.keys(row)) : null;
  const ontologyCols: string[] = [];
  const seen = new Set<string>();

  for (const api of [...Object.keys(mapping), ...derived]) {
    const col = resolveInstanceColumnKey(api, mapping, rowKeys);
    if (seen.has(col)) continue;
    seen.add(col);
    ontologyCols.push(col);
  }

  if (ontologyCols.length === 0 && row) {
    return Object.keys(row).filter((k) => !OBJECT_METADATA_KEYS.has(k));
  }

  const extras: string[] = [];
  if (row) {
    for (const key of Object.keys(row)) {
      if (OBJECT_METADATA_KEYS.has(key)) continue;
      if (columnCoveredByOntology(key, ontologyCols, mapping)) continue;
      extras.push(key);
    }
  }
  return [...ontologyCols, ...extras];
}

/** Move title_key / name to the front when present (default OE navigation column). */
export function preferTitleColumnFirst(
  keys: string[],
  objectType?: Pick<ObjectType, "title_key" | "property_mapping"> | null,
): string[] {
  if (keys.length === 0) return keys;
  const mapping = objectType?.property_mapping ?? {};
  const rowKeys = new Set(keys);
  const candidates = [objectType?.title_key, "name", "title"].filter(
    (k): k is string => Boolean(k),
  );
  for (const api of candidates) {
    const col = resolveInstanceColumnKey(api, mapping, rowKeys);
    if (keys.includes(col)) {
      if (keys[0] === col) return keys;
      return [col, ...keys.filter((k) => k !== col)];
    }
  }
  return keys;
}

export function urnShortName(urn: string): string {
  const parts = urn.split(":");
  return parts[parts.length - 1] ?? urn;
}

/** Turn an API / column name into a short label (`account_closed` → `Account closed`). */
export function humanizeApiName(name: string): string {
  const local = name.includes(".") ? (name.split(".").pop() ?? name) : name;
  const spaced = local
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\s+/g, " ")
    .trim();
  if (!spaced) return name;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
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

/** Parse `hl:{tenant}:{workspace}:instance:{ObjectType}/{id}` into Explorer params. */
export function parseInstanceUrn(instanceUrn: string): { type: string; id: string } | null {
  const segment = instanceUrn.split(":").at(-1);
  if (!segment || !segment.includes("/")) return null;
  const slash = segment.indexOf("/");
  const type = segment.slice(0, slash);
  const id = segment.slice(slash + 1);
  return type && id ? { type, id } : null;
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

/** True when an Action Type targets this ObjectType directly or via an implemented interface. */
export function actionTargetsObjectType(
  action: { target_object_type?: string | null; target_interface?: string | null },
  objectTypeName: string,
  implementedInterfaces: string[] = [],
): boolean {
  return (
    action.target_object_type === objectTypeName ||
    (!!action.target_interface && implementedInterfaces.includes(action.target_interface))
  );
}

export function actionsForObjectType<T extends { target_object_type?: string | null; target_interface?: string | null }>(
  actions: T[],
  objectTypeName: string,
  implementedInterfaces: string[] = [],
): T[] {
  return actions.filter((action) => actionTargetsObjectType(action, objectTypeName, implementedInterfaces));
}

/** FK source columns on this ObjectType → related ObjectType name. */
export function fkFieldTargetsFromRelations(
  relationTypes: Array<{
    source_object_type_urn: string;
    source_property: string;
    target_object_type_urn: string;
  }>,
  objectTypeName: string,
): Map<string, string> {
  const map = new Map<string, string>();
  for (const relation of relationTypes) {
    if (urnShortName(relation.source_object_type_urn) !== objectTypeName) continue;
    map.set(camelToSnake(relation.source_property), urnShortName(relation.target_object_type_urn));
  }
  return map;
}

export function principalsDisplayByUrn(
  principals?: Array<{ urn: string; display_name: string }> | null,
): Map<string, string> {
  const map = new Map<string, string>();
  for (const principal of principals ?? []) {
    map.set(principal.urn, principal.display_name);
  }
  return map;
}

/** One low-risk, single-edit inline action per property — shared by table and detail views. */
export function computeInlineEditableActions(
  type: string,
  implementedInterfaces: string[],
  allActions: ActionDefinition[],
): Map<string, ActionDefinition> {
  const relevant = actionsForObjectType(allActions, type, implementedInterfaces);
  const byProperty = new Map<string, ActionDefinition[]>();
  for (const action of relevant) {
    if (action.risk_level !== "low") continue;
    const edits = action.edits ?? [];
    const parameters = action.parameters ?? [];
    if (edits.length !== 1 || parameters.length !== 1) continue;
    const [edit] = edits;
    const [parameter] = parameters;
    if ((edit.kind ?? "modify_property") !== "modify_property") continue;
    if (!edit.property || edit.source !== "parameter" || edit.parameter_name !== parameter.name) continue;
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
  /** Side of the RelationType relative to the viewed object. */
  side?: "source" | "target";
  visibility?: "prominent" | "normal" | "hidden";
  cardinality?: string;
  pluralLabel?: string;
}

const VIS_RANK: Record<string, number> = { prominent: 0, normal: 1, hidden: 2 };

/**
 * Build RelatedLink entries for an ObjectType from RelationTypes.
 * Hides sides with visibility=hidden; sorts prominent first.
 */
export function buildRelatedLinksForObjectType(
  objectType: string,
  relationTypes: Array<{
    name: string;
    source_object_type_urn: string;
    target_object_type_urn: string;
    source_api_name?: string;
    target_api_name?: string;
    target_property?: string;
    source_display_name?: string;
    target_display_name?: string;
    source_plural_display_name?: string;
    target_plural_display_name?: string;
    source_visibility?: string;
    target_visibility?: string;
    cardinality?: string;
  }>,
): RelatedLink[] {
  const links: RelatedLink[] = [];
  for (const r of relationTypes) {
    if (isEphemeralTestName(r.name) || isEphemeralTestName(r.source_api_name) || isEphemeralTestName(r.target_api_name)) {
      continue;
    }
    const sourceType = urnShortName(r.source_object_type_urn);
    const targetType = urnShortName(r.target_object_type_urn);
    const localName = r.name.includes(".") ? r.name.split(".").slice(1).join(".") : r.name;
    const fwd = (r.source_api_name || localName).trim() || localName;
    const rev = ((r.target_api_name || r.target_property || "") as string).trim();
    if (sourceType === objectType) {
      const visibility = (r.source_visibility as RelatedLink["visibility"]) ?? "normal";
      if (visibility !== "hidden") {
        links.push({
          linkName: fwd,
          label: `${r.source_display_name || humanizeApiName(fwd)} → ${targetType}`,
          relatedType: targetType,
          side: "source",
          visibility,
          cardinality: r.cardinality,
          pluralLabel: r.source_plural_display_name || r.source_display_name || humanizeApiName(fwd),
        });
      }
    }
    if (targetType === objectType && rev) {
      const visibility = (r.target_visibility as RelatedLink["visibility"]) ?? "normal";
      if (visibility !== "hidden") {
        links.push({
          linkName: rev,
          label: `${r.target_display_name || humanizeApiName(rev)} ← ${sourceType}`,
          relatedType: sourceType,
          side: "target",
          visibility,
          cardinality: r.cardinality,
          pluralLabel: r.target_plural_display_name || r.target_display_name || humanizeApiName(rev),
        });
      }
    }
  }
  return links.sort(
    (a, b) =>
      (VIS_RANK[a.visibility ?? "normal"] ?? 1) - (VIS_RANK[b.visibility ?? "normal"] ?? 1) ||
      a.label.localeCompare(b.label),
  );
}

