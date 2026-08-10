import type { ActionDefinition } from "../../api/knowledge";
import { camelToSnake } from "../common/propertyFormatUtils";

/** Materialisation metadata keys stripped from user-facing property lists. */
export const OBJECT_METADATA_KEYS = new Set(["materializedAt", "sourceLagSeconds", "degraded", "_maskedFields"]);

export function urnShortName(urn: string): string {
  const parts = urn.split(":");
  return parts[parts.length - 1] ?? urn;
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
