import type {
  DerivedPropertyLinkAggregate,
  DerivedPropertyStructReducer,
  DerivedPropertyValue,
  ObjectType,
  RelationType,
} from "../../api/knowledge";

export type DerivedKind = "function" | "link_aggregate" | "struct_reducer";

export type LinkAggregateKind = DerivedPropertyLinkAggregate["aggregate"];
export type StructReducerKind = DerivedPropertyStructReducer["reducer"];

export interface EditableDerivedProperty {
  name: string;
  kind: DerivedKind;
  functionName: string;
  path: string[];
  aggregate: LinkAggregateKind;
  relatedProperty: string;
  collectLimit: number;
  arrayProperty: string;
  reducer: StructReducerKind;
  by: string;
}

export const LINK_AGGREGATES: LinkAggregateKind[] = [
  "count",
  "sum",
  "avg",
  "min",
  "max",
  "collect_list",
  "collect_set",
];

export const STRUCT_REDUCERS: StructReducerKind[] = [
  "first",
  "last",
  "latest",
  "earliest",
  "max",
  "min",
];

export function linkNamesFromType(objectTypeName: string, relations: RelationType[]): string[] {
  const names: string[] = [];
  for (const relation of relations) {
    const source = relation.source_object_type_urn.split(":").pop() ?? "";
    const target = relation.target_object_type_urn.split(":").pop() ?? "";
    const local = relation.name.includes(".") ? relation.name.split(".", 2)[1]! : relation.name;
    if (source === objectTypeName) names.push(local);
    if (target === objectTypeName && relation.target_property) names.push(relation.target_property);
  }
  return [...new Set(names)].sort();
}

export function farSideTypeName(
  objectTypeName: string,
  linkName: string,
  relations: RelationType[],
): string | null {
  for (const relation of relations) {
    const source = relation.source_object_type_urn.split(":").pop() ?? "";
    const target = relation.target_object_type_urn.split(":").pop() ?? "";
    const local = relation.name.includes(".") ? relation.name.split(".", 2)[1]! : relation.name;
    if (source === objectTypeName && local === linkName) return target;
    if (target === objectTypeName && relation.target_property === linkName) return source;
  }
  return null;
}

export function typeAfterPath(
  startType: string,
  path: string[],
  relations: RelationType[],
): string | null {
  let current = startType;
  for (const hop of path) {
    if (!hop) return null;
    const next = farSideTypeName(current, hop, relations);
    if (!next) return null;
    current = next;
  }
  return current;
}

export function emptyDerivedProperty(seedName = "derived"): EditableDerivedProperty {
  return {
    name: seedName,
    kind: "link_aggregate",
    functionName: "",
    path: [""],
    aggregate: "count",
    relatedProperty: "",
    collectLimit: 10,
    arrayProperty: "",
    reducer: "first",
    by: "",
  };
}

export function buildEditableDerived(
  derived: Record<string, DerivedPropertyValue> = {},
): EditableDerivedProperty[] {
  return Object.entries(derived).map(([name, value]) => {
    const base = emptyDerivedProperty(name);
    if (typeof value === "string") {
      return { ...base, kind: "function", functionName: value };
    }
    if (value.kind === "link_aggregate") {
      const path =
        value.path && value.path.length > 0
          ? [...value.path]
          : value.relation
            ? [value.relation]
            : [""];
      return {
        ...base,
        kind: "link_aggregate",
        path,
        aggregate: value.aggregate,
        relatedProperty: value.property ?? "",
        collectLimit: value.collect_limit ?? 10,
      };
    }
    return {
      ...base,
      kind: "struct_reducer",
      arrayProperty: value.property,
      reducer: value.reducer,
      by: value.by ?? "",
    };
  });
}

export function serializeDerivedProperties(
  properties: EditableDerivedProperty[],
): Record<string, DerivedPropertyValue> {
  const out: Record<string, DerivedPropertyValue> = {};
  for (const prop of properties) {
    const name = prop.name.trim();
    if (!name) continue;
    if (prop.kind === "function") {
      if (!prop.functionName.trim()) continue;
      out[name] = prop.functionName.trim();
      continue;
    }
    if (prop.kind === "struct_reducer") {
      if (!prop.arrayProperty.trim()) continue;
      const rule: DerivedPropertyStructReducer = {
        kind: "struct_reducer",
        property: prop.arrayProperty.trim(),
        reducer: prop.reducer,
      };
      if (prop.by.trim()) rule.by = prop.by.trim();
      out[name] = rule;
      continue;
    }
    const path = prop.path.map((h) => h.trim()).filter(Boolean);
    if (path.length === 0 || path.length > 3) continue;
    const rule: DerivedPropertyLinkAggregate = {
      kind: "link_aggregate",
      path,
      aggregate: prop.aggregate,
    };
    if (path.length === 1) rule.relation = path[0];
    if (prop.aggregate !== "count" && prop.relatedProperty.trim()) {
      rule.property = prop.relatedProperty.trim();
    }
    if (prop.aggregate === "collect_list" || prop.aggregate === "collect_set") {
      rule.collect_limit = prop.collectLimit > 0 ? prop.collectLimit : 10;
    }
    out[name] = rule;
  }
  return out;
}

export function arrayPropertyNames(objectType: ObjectType): string[] {
  const types = objectType.property_types ?? {};
  return Object.keys(types)
    .filter((name) => types[name]?.kind === "array")
    .sort();
}

export function propertyNamesOfType(
  objectTypeName: string,
  objectTypes: ObjectType[],
): string[] {
  const ot = objectTypes.find((t) => t.name === objectTypeName);
  return ot ? Object.keys(ot.property_mapping ?? {}).sort() : [];
}
