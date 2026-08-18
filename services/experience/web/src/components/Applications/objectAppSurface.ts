import type { Application, ApplicationDefinition } from "../../api/experience";
import { isObjectAppSurface } from "../../api/experience";
import type { ActionType, ObjectType, RelationType } from "../../api/knowledge";
import { urnShortName, type RelatedLink } from "../ObjectExplorer/objectExplorerUtils";

export const EMPTY_OBJECT_APP_LINKS: string[] = [];

export function defaultApplicationDefinition(
  objectTypes: Array<Pick<ObjectType, "name" | "visibility">>,
  actions: Array<Pick<ActionType, "name" | "target_object_type" | "risk_level">>,
): ApplicationDefinition {
  const objectType = objectTypes.find((type) => type.visibility !== "hidden")?.name;
  if (!objectType) {
    return { surfaces: [], bindings: [], actionRefs: [] };
  }
  const action = actions.find(
    (candidate) => candidate.target_object_type === objectType && candidate.risk_level !== "high",
  );
  return {
    surfaces: [{ type: "objectApp", objectType, route: `/apps/${objectType}` }],
    bindings: [
      { component: "table", objectType },
      { component: "detail", objectType },
    ],
    actionRefs: action ? [{ action: action.name, riskClass: action.risk_level }] : [],
  };
}

export function resolveObjectAppSurface(application: Application): {
  objectType: string;
  objectSet?: string;
  links: string[];
} | null {
  const surface = application.definition.surfaces.find(isObjectAppSurface);
  if (!surface?.objectType) return null;
  return {
    objectType: surface.objectType,
    objectSet: surface.objectSet || undefined,
    links: surface.links ?? [],
  };
}

export function declaredRelatedLinks(objectType: string, declared: string[], relationTypes: RelationType[]): RelatedLink[] {
  const wantedLinks = new Set(declared);
  if (wantedLinks.size === 0) return [];

  const links: RelatedLink[] = [];
  for (const relation of relationTypes) {
    const sourceType = urnShortName(relation.source_object_type_urn);
    const targetType = urnShortName(relation.target_object_type_urn);
    const localName = relation.name.includes(".") ? relation.name.split(".").slice(1).join(".") : relation.name;
    const forwardName = (relation.source_api_name || localName).trim() || localName;
    const reverseName = (relation.target_api_name || relation.target_property || "").trim();

    if (sourceType === objectType && (wantedLinks.has(forwardName) || wantedLinks.has(localName))) {
      const linkName = wantedLinks.has(forwardName) ? forwardName : localName;
      links.push({
        linkName,
        label: `${relation.source_display_name || linkName} → ${targetType}`,
        relatedType: targetType,
      });
    }
    if (targetType === objectType && reverseName && (wantedLinks.has(reverseName) || wantedLinks.has(relation.target_property || ""))) {
      const linkName = wantedLinks.has(reverseName) ? reverseName : (relation.target_property || reverseName);
      links.push({
        linkName,
        label: `${relation.target_display_name || linkName} ← ${sourceType}`,
        relatedType: sourceType,
      });
    }
  }
  return links;
}
