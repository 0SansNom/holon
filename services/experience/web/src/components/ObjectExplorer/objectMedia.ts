import { hasTypeClass } from "../Ontology/typeClassUtils";
import { resolveDisplayTypeRule, resolvePropertyTypeRule } from "../Ontology/propertyEditorUtils";
import type { ObjectType, SharedPropertyType } from "../../api/knowledge";
import { OBJECT_METADATA_KEYS } from "./objectExplorerUtils";

export type ObjectMediaItem = {
  property: string;
  url: string;
  kind: "media_url" | "icon";
};

/** Collect hubble:media_url and hubble:icon string properties for the Media gallery. */
export function collectObjectMediaItems(
  object: Record<string, unknown>,
  objectType?: ObjectType | null,
  sharedPropertyTypes: SharedPropertyType[] = [],
): ObjectMediaItem[] {
  const items: ObjectMediaItem[] = [];
  for (const key of Object.keys(object)) {
    if (OBJECT_METADATA_KEYS.has(key)) continue;
    const value = object[key];
    if (typeof value !== "string" || !value.trim()) continue;
    const typeRule = resolveDisplayTypeRule(
      resolvePropertyTypeRule(key, objectType?.property_types, objectType?.property_mapping),
      sharedPropertyTypes,
    );
    if (hasTypeClass(typeRule?.type_classes, "hubble", "media_url")) {
      items.push({ property: key, url: value, kind: "media_url" });
    } else if (hasTypeClass(typeRule?.type_classes, "hubble", "icon")) {
      items.push({ property: key, url: value, kind: "icon" });
    }
  }
  return items;
}
