import type { InterfaceLinkConstraint, InterfaceType } from "../../api/knowledge/types";

export type EffectiveInterfaceContract = {
  name: string;
  parent_interfaces: string[];
  required_properties: string[];
  required_actions: string[];
  property_types: NonNullable<InterfaceType["property_types"]>;
  link_constraints: InterfaceLinkConstraint[];
};

/** Merge parents then local — mirrors Knowledge `effective_interface_contract`. */
export function effectiveInterfaceContract(
  iface: InterfaceType,
  byName: Map<string, InterfaceType> | Record<string, InterfaceType>,
  visiting: Set<string> = new Set(),
): EffectiveInterfaceContract {
  const lookup = (name: string): InterfaceType | undefined =>
    byName instanceof Map ? byName.get(name) : byName[name];

  if (visiting.has(iface.name)) {
    return {
      name: iface.name,
      parent_interfaces: iface.parent_interfaces ?? [],
      required_properties: [...(iface.required_properties ?? [])],
      required_actions: [...(iface.required_actions ?? [])],
      property_types: { ...(iface.property_types ?? {}) },
      link_constraints: [...(iface.link_constraints ?? [])],
    };
  }
  visiting.add(iface.name);

  const required_properties: string[] = [];
  const required_actions: string[] = [];
  const property_types: NonNullable<InterfaceType["property_types"]> = {};
  const linkByApi = new Map<string, InterfaceLinkConstraint>();
  const seenProps = new Set<string>();
  const seenActions = new Set<string>();

  for (const parentName of iface.parent_interfaces ?? []) {
    const parent = lookup(parentName);
    if (!parent) continue;
    const eff = effectiveInterfaceContract(parent, byName, visiting);
    for (const prop of eff.required_properties) {
      if (!seenProps.has(prop)) {
        seenProps.add(prop);
        required_properties.push(prop);
      }
    }
    for (const action of eff.required_actions) {
      if (!seenActions.has(action)) {
        seenActions.add(action);
        required_actions.push(action);
      }
    }
    Object.assign(property_types, eff.property_types);
    for (const c of eff.link_constraints) {
      linkByApi.set(c.api_name, c);
    }
  }

  for (const prop of iface.required_properties ?? []) {
    if (!seenProps.has(prop)) {
      seenProps.add(prop);
      required_properties.push(prop);
    }
  }
  for (const action of iface.required_actions ?? []) {
    if (!seenActions.has(action)) {
      seenActions.add(action);
      required_actions.push(action);
    }
  }
  Object.assign(property_types, iface.property_types ?? {});
  for (const c of iface.link_constraints ?? []) {
    linkByApi.set(c.api_name, c);
  }

  return {
    name: iface.name,
    parent_interfaces: iface.parent_interfaces ?? [],
    required_properties,
    required_actions,
    property_types,
    link_constraints: [...linkByApi.values()],
  };
}
