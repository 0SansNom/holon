import { useMemo, useRef, useState } from "react";
import {
  Button,
  Dialog,
  DialogBody,
  DialogFooter,
  FormGroup,
  HTMLSelect,
  InputGroup,
  Menu,
  MenuItem,
  PopoverNext,
  Tag,
} from "@blueprintjs/core";
import { useNavigate } from "@tanstack/react-router";
import {
  useActionTypes,
  useInterfaces,
  useObjectTypes,
  useRelationTypes,
  useSharedPropertyTypes,
  useValueTypes,
  type BranchKind,
} from "../../api/hooks";
import { usePaletteIntentStore, type PaletteIntent } from "../../store/paletteIntent";
import { BranchesDialog } from "./BranchesDialog";
import type { OntologyTabId } from "./ontologyTabs";
import { urnShortName } from "../ObjectExplorer/objectExplorerUtils";

type CreateItem =
  | { kind: "intent"; label: string; intent: PaletteIntent; tab: OntologyTabId }
  | { kind: "route"; label: string; to: string };

const CREATE_ITEMS: CreateItem[] = [
  { kind: "route", label: "ObjectType from dataset…", to: "/sources" },
  { kind: "intent", label: "Interface", intent: "create-interface", tab: "interfaces" },
  { kind: "intent", label: "RelationType", intent: "create-relation-type", tab: "relation-types" },
  { kind: "intent", label: "Value Type", intent: "create-value-type", tab: "value-types" },
  {
    kind: "intent",
    label: "Shared Property Type",
    intent: "create-shared-property-type",
    tab: "shared-property-types",
  },
  { kind: "intent", label: "Action Type", intent: "create-action-type", tab: "action-types" },
  {
    kind: "intent",
    label: "Object Type Group",
    intent: "create-object-type-group",
    tab: "object-type-groups",
  },
  { kind: "intent", label: "Object Set", intent: "create-object-set", tab: "object-sets" },
];

type SearchHit = {
  id: string;
  kindLabel: string;
  name: string;
  hint?: string;
  onOpen: () => void;
};

type BranchTarget = {
  kind: BranchKind;
  resourceName: string;
  currentDefinition: Record<string, unknown>;
};

const BRANCH_KINDS: { value: BranchKind; label: string }[] = [
  { value: "object_type", label: "ObjectType" },
  { value: "relation_type", label: "RelationType" },
  { value: "action_type", label: "Action Type" },
  { value: "interface_type", label: "Interface" },
  { value: "value_type", label: "Value Type" },
  { value: "shared_property_type", label: "Shared Property Type" },
];

function normalizeQuery(q: string): string {
  return q.trim().toLowerCase();
}

/** Foundry-shaped OM top bar: search resources, create menu, branches launcher. */
export function OntologyChrome() {
  const navigate = useNavigate();
  const trigger = usePaletteIntentStore((s) => s.trigger);
  const { data: objectTypes = [] } = useObjectTypes();
  const { data: relationTypes = [] } = useRelationTypes();
  const { data: actionTypes = [] } = useActionTypes();
  const { data: interfaces = [] } = useInterfaces();
  const { data: valueTypes = [] } = useValueTypes();
  const { data: sharedPropertyTypes = [] } = useSharedPropertyTypes();

  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [branchesPickerOpen, setBranchesPickerOpen] = useState(false);
  const [branchKind, setBranchKind] = useState<BranchKind>("object_type");
  const [branchResource, setBranchResource] = useState("");
  const [branchTarget, setBranchTarget] = useState<BranchTarget | null>(null);
  const searchWrapRef = useRef<HTMLDivElement>(null);

  const branchResourceOptions = useMemo(() => {
    switch (branchKind) {
      case "object_type":
        return objectTypes.map((ot) => ot.name);
      case "relation_type":
        return relationTypes.map((rt) => rt.name);
      case "action_type":
        return actionTypes.map((at) => at.name);
      case "interface_type":
        return interfaces.map((i) => i.name);
      case "value_type":
        return valueTypes.map((vt) => vt.name);
      case "shared_property_type":
        return sharedPropertyTypes.map((spt) => spt.api_name);
      default:
        return [];
    }
  }, [
    branchKind,
    objectTypes,
    relationTypes,
    actionTypes,
    interfaces,
    valueTypes,
    sharedPropertyTypes,
  ]);

  const hits = useMemo(() => {
    const q = normalizeQuery(query);
    if (q.length < 1) return [] as SearchHit[];

    const out: SearchHit[] = [];
    const push = (hit: SearchHit) => {
      if (out.length >= 12) return;
      out.push(hit);
    };

    for (const ot of objectTypes) {
      if (!ot.name.toLowerCase().includes(q) && !(ot.description ?? "").toLowerCase().includes(q)) continue;
      push({
        id: `ot:${ot.name}`,
        kindLabel: "ObjectType",
        name: ot.name,
        hint: ot.lifecycle_status ?? undefined,
        onOpen: () =>
          void navigate({ to: "/ontology/object-types/$name", params: { name: ot.name } }),
      });
    }
    for (const rt of relationTypes) {
      if (!rt.name.toLowerCase().includes(q)) continue;
      push({
        id: `rt:${rt.name}`,
        kindLabel: "RelationType",
        name: rt.name,
        hint: `${urnShortName(rt.source_object_type_urn)} ↔ ${urnShortName(rt.target_object_type_urn)}`,
        onOpen: () =>
          void navigate({ to: "/ontology/relation-types/$name", params: { name: rt.name } }),
      });
    }
    for (const at of actionTypes) {
      if (!at.name.toLowerCase().includes(q) && !(at.description ?? "").toLowerCase().includes(q)) continue;
      push({
        id: `at:${at.name}`,
        kindLabel: "Action",
        name: at.name,
        hint: at.target_object_type ?? at.target_interface ?? undefined,
        onOpen: () =>
          void navigate({ to: "/ontology/action-types/$name", params: { name: at.name } }),
      });
    }
    for (const iface of interfaces) {
      if (!iface.name.toLowerCase().includes(q)) continue;
      push({
        id: `if:${iface.name}`,
        kindLabel: "Interface",
        name: iface.name,
        onOpen: () => void navigate({ to: "/ontology", search: { tab: "interfaces" } }),
      });
    }
    for (const vt of valueTypes) {
      if (!vt.name.toLowerCase().includes(q)) continue;
      push({
        id: `vt:${vt.name}`,
        kindLabel: "ValueType",
        name: vt.name,
        hint: vt.base_type,
        onOpen: () => void navigate({ to: "/ontology", search: { tab: "value-types" } }),
      });
    }
    for (const spt of sharedPropertyTypes) {
      if (!spt.api_name.toLowerCase().includes(q) && !spt.display_name.toLowerCase().includes(q)) continue;
      push({
        id: `spt:${spt.api_name}`,
        kindLabel: "SPT",
        name: spt.api_name,
        hint: spt.display_name,
        onOpen: () => void navigate({ to: "/ontology", search: { tab: "shared-property-types" } }),
      });
    }
    return out;
  }, [
    query,
    objectTypes,
    relationTypes,
    actionTypes,
    interfaces,
    valueTypes,
    sharedPropertyTypes,
    navigate,
  ]);

  function goCreate(item: CreateItem) {
    if (item.kind === "route") {
      void navigate({ to: item.to });
      return;
    }
    void navigate({
      to: "/ontology",
      search: { tab: item.tab },
    });
    // Intent fires after navigation so the tab mounts and opens its dialog.
    window.setTimeout(() => trigger(item.intent), 0);
  }

  function openBranchTarget() {
    const name = branchResource.trim();
    if (!name) return;
    const def = buildBranchDefinition(branchKind, name, {
      objectTypes,
      relationTypes,
      actionTypes,
      interfaces,
      valueTypes,
      sharedPropertyTypes,
    });
    if (!def) return;
    setBranchesPickerOpen(false);
    setBranchTarget({ kind: branchKind, resourceName: name, currentDefinition: def });
  }

  return (
    <>
      <div className="hl-om-chrome">
        <div className="hl-om-chrome-search" ref={searchWrapRef}>
          <InputGroup
            leftIcon="search"
            placeholder="Search Ontology resources…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSearchOpen(true);
            }}
            onFocus={() => setSearchOpen(true)}
            onBlur={() => {
              // Allow click on hit before closing.
              window.setTimeout(() => setSearchOpen(false), 150);
            }}
            className="hl-om-chrome-search-input"
          />
          {searchOpen && query.trim() && (
            <div className="hl-om-chrome-hits" role="listbox">
              {hits.length === 0 ? (
                <div className="hl-om-chrome-hit hl-text-muted">No matches.</div>
              ) : (
                hits.map((hit) => (
                  <button
                    key={hit.id}
                    type="button"
                    className="hl-om-chrome-hit"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      hit.onOpen();
                      setQuery("");
                      setSearchOpen(false);
                    }}
                  >
                    <Tag minimal className="hl-om-chrome-hit-kind">
                      {hit.kindLabel}
                    </Tag>
                    <span className="hl-mono">{hit.name}</span>
                    {hit.hint ? <span className="hl-text-muted-sm">{hit.hint}</span> : null}
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        <Button icon="git-branch" onClick={() => setBranchesPickerOpen(true)}>
          Branches
        </Button>

        <PopoverNext
          placement="bottom-end"
          content={
            <Menu>
              {CREATE_ITEMS.map((item) => (
                <MenuItem
                  key={item.label}
                  icon="add"
                  text={item.label}
                  onClick={() => goCreate(item)}
                />
              ))}
            </Menu>
          }
        >
          <Button intent="primary" icon="add" rightIcon="caret-down">
            Create
          </Button>
        </PopoverNext>
      </div>

      <Dialog
        isOpen={branchesPickerOpen}
        onClose={() => setBranchesPickerOpen(false)}
        title="Open branches"
      >
        <DialogBody>
          <p className="hl-text-muted-sm hl-mb-md">
            Pick a resource to open its branch review dialog (same as per-card Branches).
          </p>
          <FormGroup label="Resource kind">
            <HTMLSelect
              fill
              value={branchKind}
              onChange={(e) => {
                setBranchKind(e.target.value as BranchKind);
                setBranchResource("");
              }}
            >
              {BRANCH_KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </HTMLSelect>
          </FormGroup>
          <FormGroup label="Resource">
            <HTMLSelect fill value={branchResource} onChange={(e) => setBranchResource(e.target.value)}>
              <option value="">Select…</option>
              {branchResourceOptions.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </HTMLSelect>
          </FormGroup>
        </DialogBody>
        <DialogFooter
          actions={
            <>
              <Button onClick={() => setBranchesPickerOpen(false)}>Cancel</Button>
              <Button intent="primary" disabled={!branchResource} onClick={openBranchTarget}>
                Open branches
              </Button>
            </>
          }
        />
      </Dialog>

      {branchTarget && (
        <BranchesDialog
          kind={branchTarget.kind}
          resourceName={branchTarget.resourceName}
          currentDefinition={branchTarget.currentDefinition}
          onClose={() => setBranchTarget(null)}
        />
      )}
    </>
  );
}

function buildBranchDefinition(
  kind: BranchKind,
  name: string,
  data: {
    objectTypes: ReturnType<typeof useObjectTypes>["data"];
    relationTypes: ReturnType<typeof useRelationTypes>["data"];
    actionTypes: ReturnType<typeof useActionTypes>["data"];
    interfaces: ReturnType<typeof useInterfaces>["data"];
    valueTypes: ReturnType<typeof useValueTypes>["data"];
    sharedPropertyTypes: ReturnType<typeof useSharedPropertyTypes>["data"];
  },
): Record<string, unknown> | null {
  if (kind === "object_type") {
    const ot = (data.objectTypes ?? []).find((x) => x.name === name);
    if (!ot) return null;
    return {
      property_mapping: ot.property_mapping,
      description: ot.description,
      implements: ot.implements ?? [],
      derived_properties: ot.derived_properties ?? {},
      project_urn: ot.project_urn ?? null,
      markings: ot.markings ?? [],
      property_formats: ot.property_formats,
      conditional_formats: ot.conditional_formats ?? {},
      property_types: ot.property_types ?? {},
      link_constraint_bindings: ot.link_constraint_bindings ?? {},
    };
  }
  if (kind === "relation_type") {
    const rt = (data.relationTypes ?? []).find((x) => x.name === name);
    if (!rt) return null;
    return {
      source_object_type: urnShortName(rt.source_object_type_urn),
      target_object_type: urnShortName(rt.target_object_type_urn),
      source_object_type_urn: rt.source_object_type_urn,
      target_object_type_urn: rt.target_object_type_urn,
      source_property: rt.source_property,
      target_property: rt.target_property,
      cardinality: rt.cardinality,
      storage_kind: rt.storage_kind ?? "foreign_key",
      join_dataset_urn: rt.join_dataset_urn ?? null,
      join_source_column: rt.join_source_column ?? null,
      join_target_column: rt.join_target_column ?? null,
      mid_object_type_urn: rt.mid_object_type_urn ?? null,
      mid_object_type: rt.mid_object_type_urn ? urnShortName(rt.mid_object_type_urn) : null,
      mid_source_property: rt.mid_source_property ?? null,
      mid_target_property: rt.mid_target_property ?? null,
      source_display_name: rt.source_display_name ?? "",
      source_plural_display_name: rt.source_plural_display_name ?? "",
      source_api_name: rt.source_api_name ?? "",
      source_visibility: rt.source_visibility ?? "normal",
      target_display_name: rt.target_display_name ?? "",
      target_plural_display_name: rt.target_plural_display_name ?? "",
      target_api_name: rt.target_api_name ?? "",
      target_visibility: rt.target_visibility ?? "normal",
      lifecycle_status: rt.lifecycle_status ?? "experimental",
      type_classes: rt.type_classes ?? [],
      project_urn: rt.project_urn ?? null,
    };
  }
  if (kind === "action_type") {
    const at = (data.actionTypes ?? []).find((x) => x.name === name);
    if (!at) return null;
    return {
      target_object_type: at.target_object_type,
      target_interface: at.target_interface,
      required_permission: at.required_permission,
      risk_level: at.risk_level,
      description: at.description,
      parameters: at.parameters,
      edits: at.edits,
      submission_criteria: at.submission_criteria,
      function_side_effect: at.function_side_effect,
      writeback_dataset: at.writeback_dataset,
      edit_function: at.edit_function,
      sections: at.sections,
      type_classes: at.type_classes ?? [],
      lifecycle_status: at.lifecycle_status,
      deprecation_reason: at.deprecation_reason,
      deprecation_deadline: at.deprecation_deadline,
      replacement_urn: at.replacement_urn,
    };
  }
  if (kind === "interface_type") {
    const iface = (data.interfaces ?? []).find((x) => x.name === name);
    if (!iface) return null;
    return {
      required_properties: iface.required_properties,
      required_actions: iface.required_actions,
      property_types: iface.property_types ?? {},
      link_constraints: iface.link_constraints ?? [],
      parent_interfaces: iface.parent_interfaces ?? [],
      description: iface.description,
      lifecycle_status: iface.lifecycle_status,
      deprecation_reason: iface.deprecation_reason,
      deprecation_deadline: iface.deprecation_deadline,
      replacement_urn: iface.replacement_urn,
    };
  }
  if (kind === "value_type") {
    const vt = (data.valueTypes ?? []).find((x) => x.name === name);
    if (!vt) return null;
    return {
      base_type: vt.base_type,
      format_regex: vt.format_regex,
      format_regex_match: vt.format_regex_match,
      constraints: vt.constraints,
      description: vt.description,
      api_name: vt.api_name,
      display_name: vt.display_name,
      example_value: vt.example_value,
      lifecycle_status: vt.lifecycle_status,
      project_urn: vt.project_urn,
    };
  }
  if (kind === "shared_property_type") {
    const spt = (data.sharedPropertyTypes ?? []).find((x) => x.api_name === name);
    if (!spt) return null;
    return {
      display_name: spt.display_name,
      value_type: spt.value_type,
      struct_properties: spt.struct_properties,
      description: spt.description,
      visibility: spt.visibility,
      render_hints: spt.render_hints,
      type_classes: spt.type_classes,
      property_format: spt.property_format,
      aliases: spt.aliases,
      project_urn: spt.project_urn,
    };
  }
  return null;
}
