/** Foundry lifecycle_status values used across Ontology Manager UI. */

export const REGISTRY_LIFECYCLE_STATUSES = ["experimental", "active", "deprecated", "example"] as const;
export const OBJECT_TYPE_LIFECYCLE_STATUSES = [...REGISTRY_LIFECYCLE_STATUSES, "promoted"] as const;
export const PROPERTY_LIFECYCLE_STATUSES = REGISTRY_LIFECYCLE_STATUSES;

export type RegistryLifecycleStatus = (typeof REGISTRY_LIFECYCLE_STATUSES)[number];
export type ObjectTypeLifecycleStatus = (typeof OBJECT_TYPE_LIFECYCLE_STATUSES)[number];
export type PropertyLifecycleStatus = (typeof PROPERTY_LIFECYCLE_STATUSES)[number];
