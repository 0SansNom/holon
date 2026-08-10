import type { ResourceBranchKind } from "./knowledge";

/** Centralised React Query keys — keeps invalidation consistent across hooks. */
export const queryKeys = {
  objectTypes: () => ["objectTypes"] as const,
  objectType: (name: string) => ["objectType", name] as const,
  objectTypeVersions: (name: string) => ["objectTypeVersions", name] as const,
  objects: (objectType: string) => ["objects", objectType] as const,
  object: (objectType: string, id: string | number) => ["object", objectType, id] as const,
  datasetPreview: (datasetName: string) => ["datasetPreview", datasetName] as const,

  relationTypes: () => ["relationTypes"] as const,
  actions: () => ["actions"] as const,
  valueTypes: () => ["valueTypes"] as const,
  sharedPropertyTypes: () => ["sharedPropertyTypes"] as const,
  actionTypes: () => ["actionTypes"] as const,
  interfaces: () => ["interfaces"] as const,
  markings: () => ["markings"] as const,
  objectTypeGroups: () => ["objectTypeGroups"] as const,
  ontologyHealthCheck: () => ["ontologyHealthCheck"] as const,

  branches: (kind: string, resourceName: string) => ["branches", kind, resourceName] as const,
  branchReviews: (kind: string, resourceName: string, branchName: string) =>
    ["branchReviews", kind, resourceName, branchName] as const,

  lineage: (urn: string) => ["lineage", urn] as const,
  objectGraph: (objectType: string, id: string | number, hops: number) =>
    ["objectGraph", objectType, id, hops] as const,
  objectTimeline: (objectType: string, id: string | number) => ["objectTimeline", objectType, id] as const,
  objectLinks: (objectType: string, id: string | number, linkName: string) =>
    ["objectLinks", objectType, id, linkName] as const,

  search: (q: string, objectType?: string, from?: number, size?: number) =>
    ["search", q, objectType, from, size] as const,
  glossary: () => ["glossary"] as const,

  applications: () => ["applications"] as const,
  application: (name: string) => ["application", name] as const,
  applicationDashboard: (name: string) => ["applicationDashboard", name] as const,
  objectAppData: (name: string) => ["objectAppData", name] as const,
  objectAppDetail: (name: string, id: string | number) => ["objectAppDetail", name, id] as const,

  collections: () => ["collections"] as const,
  collection: (id: number) => ["collection", id] as const,
  resourceCollections: (urn: string) => ["resourceCollections", urn] as const,

  principals: () => ["principals"] as const,
  projects: () => ["projects"] as const,
  projectPins: (projectUrn: string) => ["projectPins", projectUrn] as const,

  sources: () => ["sources"] as const,
  syncs: () => ["syncs"] as const,
  connections: () => ["connections"] as const,

  resourceTags: () => ["resourceTags"] as const,
  tools: () => ["tools"] as const,
} as const;

export type BranchKind = "object_type" | ResourceBranchKind;

export const BRANCH_KIND_LIST_QUERY_KEY: Record<BranchKind, readonly [string]> = {
  object_type: queryKeys.objectTypes(),
  interface_type: queryKeys.interfaces(),
  relation_type: queryKeys.relationTypes(),
  value_type: queryKeys.valueTypes(),
  shared_property_type: queryKeys.sharedPropertyTypes(),
  action_type: queryKeys.actionTypes(),
};
