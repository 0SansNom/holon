export const ONTOLOGY_TABS = [
  "discover",
  "object-types",
  "interfaces",
  "relation-types",
  "value-types",
  "shared-property-types",
  "action-types",
  "object-type-groups",
  "object-sets",
  "health-check",
] as const;

export type OntologyTabId = (typeof ONTOLOGY_TABS)[number];

const ONTOLOGY_TAB_SET = new Set<string>(ONTOLOGY_TABS);

export function parseOntologyTab(raw: unknown): OntologyTabId | undefined {
  return typeof raw === "string" && ONTOLOGY_TAB_SET.has(raw) ? (raw as OntologyTabId) : undefined;
}
