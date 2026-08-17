import { KNOWLEDGE_URL, WORKSPACE_ID } from "../config";

/** Ontology API paths under `/api/ontologies/{workspace}`. */
export function ontologyUrl(path: string): string {
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${KNOWLEDGE_URL}/api/ontologies/${WORKSPACE_ID}${suffix}`;
}

/** Holon-native Knowledge paths under `/api/holon`. */
export function holonUrl(path: string): string {
  const suffix = path.startsWith("/") ? path : `/${path}`;
  return `${KNOWLEDGE_URL}/api/holon${suffix}`;
}
