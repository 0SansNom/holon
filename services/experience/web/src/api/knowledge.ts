import { KNOWLEDGE_URL } from "./config";
import { api } from "./client";

export interface ObjectType {
  urn: string;
  tenant_id: string;
  name: string;
  source_dataset_urn: string;
  property_mapping: Record<string, string>;
  classification: "public" | "internal" | "confidential";
  description: string;
  version: number;
  created_at: string;
}

export interface ObjectTypeVersion {
  id: number;
  object_type_urn: string;
  tenant_id: string;
  version: number;
  property_mapping: Record<string, string>;
  description: string;
  status: "draft" | "published";
  created_at: string;
  published_at: string | null;
}

export interface ActionDefinition {
  name: string;
  target_object_type: string;
  required_permission: string;
  risk_level: "low" | "high";
  description: string;
}

export interface LineageEdge {
  source_urn: string;
  target_urn: string;
  relation: string;
  source_column: string;
  target_property: string;
}

export interface SearchResult {
  total: number;
  results: Array<{
    urn: string;
    object_type: string;
    tenant_id: string;
    classification: string;
    text: string;
  }>;
}

export interface GlossaryTerm {
  term: string;
  definition: string;
  synonyms: string[];
  related_object_type_urn: string | null;
}

export const knowledgeApi = {
  listObjectTypes: () => api.get<ObjectType[]>(`${KNOWLEDGE_URL}/ontology`),
  getObjectType: (name: string) => api.get<ObjectType>(`${KNOWLEDGE_URL}/ontology/${name}`),
  listObjectTypeVersions: (name: string) => api.get<ObjectTypeVersion[]>(`${KNOWLEDGE_URL}/ontology/${name}/versions`),
  proposeObjectTypeVersion: (name: string, body: { description?: string; property_mapping?: Record<string, string> }) =>
    api.post<ObjectTypeVersion>(`${KNOWLEDGE_URL}/ontology/${name}/versions`, body),
  publishObjectTypeVersion: (name: string, version: number) =>
    api.post<ObjectType>(`${KNOWLEDGE_URL}/ontology/${name}/versions/${version}/publish`),

  listObjects: (objectType: string) => api.get<Array<Record<string, unknown>>>(`${KNOWLEDGE_URL}/objects/${objectType}`),
  getObject: (objectType: string, id: string | number) =>
    api.get<Record<string, unknown>>(`${KNOWLEDGE_URL}/objects/${objectType}/${id}`),

  listActions: () => api.get<ActionDefinition[]>(`${KNOWLEDGE_URL}/actions`),
  invokeAction: (objectType: string, id: string | number, localActionName: string, reason: string) =>
    api.post<Record<string, unknown>>(`${KNOWLEDGE_URL}/objects/${objectType}/${id}/actions/${localActionName}`, { reason }),

  getLineage: (urn: string) => api.get<LineageEdge[]>(`${KNOWLEDGE_URL}/lineage/${encodeURIComponent(urn)}`),

  search: (q: string) => api.get<SearchResult>(`${KNOWLEDGE_URL}/search?q=${encodeURIComponent(q)}`),

  listGlossary: () => api.get<GlossaryTerm[]>(`${KNOWLEDGE_URL}/glossary`),

  execute: (body: { object_type: string; filter_property: string; filter_value: string; operation?: "filter" | "count" }) =>
    api.post<{ planId: string; planHash: string; cached: boolean; rowCount?: number; count?: number; results?: unknown[] }>(
      `${KNOWLEDGE_URL}/execute`,
      body,
    ),

  listRelationTypes: () => api.get<Array<Record<string, unknown>>>(`${KNOWLEDGE_URL}/relation-types`),
};
