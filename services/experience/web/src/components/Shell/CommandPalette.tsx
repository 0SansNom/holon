import { useMemo } from "react";
import { useNavigate } from "@tanstack/react-router";
import { MenuItem, type IconName } from "@blueprintjs/core";
import { Omnibar } from "@blueprintjs/select";
import { useObjectTypes, useApplications, useObjectSets } from "../../api/hooks";
import { NAV_ITEMS, SEQUENTIAL_SHORTCUTS } from "./navigation";
import { usePaletteIntentStore, type PaletteIntent } from "../../store/paletteIntent";
import { objectSetBrowsePath, urnShortName } from "../ObjectExplorer/objectExplorerUtils";

const SHORTCUT_BY_PATH: Partial<Record<(typeof NAV_ITEMS)[number]["to"], string>> = Object.fromEntries(
  Object.entries(SEQUENTIAL_SHORTCUTS).map(([key, to]) => [to, `G ${key.toUpperCase()}`]),
);

const ACTIONS: Array<{ id: string; label: string; icon: IconName; intent: PaletteIntent; to: (typeof NAV_ITEMS)[number]["to"] }> = [
  { id: "action-value-type", label: "New Value Type", icon: "add", intent: "create-value-type", to: "/ontology" },
  { id: "action-interface", label: "New Interface", icon: "add", intent: "create-interface", to: "/ontology" },
  { id: "action-relation-type", label: "New RelationType", icon: "add", intent: "create-relation-type", to: "/ontology" },
  {
    id: "action-shared-property-type",
    label: "New Shared Property Type",
    icon: "add",
    intent: "create-shared-property-type",
    to: "/ontology",
  },
  { id: "action-action-type", label: "New Action Type", icon: "add", intent: "create-action-type", to: "/ontology" },
  {
    id: "action-object-type-group",
    label: "New Object Type Group",
    icon: "add",
    intent: "create-object-type-group",
    to: "/ontology",
  },
  { id: "action-object-set", label: "New Object Set", icon: "add", intent: "create-object-set", to: "/ontology" },
  { id: "action-pipeline", label: "New Pipeline", icon: "add", intent: "create-pipeline", to: "/pipelines" },
  { id: "action-project", label: "New Project", icon: "add", intent: "create-project", to: "/admin" },
  { id: "action-connect-source", label: "Connect a source", icon: "add", intent: "connect-source", to: "/sources" },
  { id: "action-connection", label: "New connection", icon: "add", intent: "create-connection", to: "/sources" },
];

interface PaletteItem {
  id: string;
  label: string;
  hint: string;
  icon: IconName;
  onSelect: () => void;
}

const PaletteOmnibar = Omnibar.ofType<PaletteItem>();

export function CommandPalette({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const { data: objectTypes } = useObjectTypes();
  const { data: applications } = useApplications();
  const { data: objectSets = [] } = useObjectSets();
  const triggerIntent = usePaletteIntentStore((s) => s.trigger);

  const items = useMemo<PaletteItem[]>(() => {
    const navItems: PaletteItem[] = NAV_ITEMS.map((item) => ({
      id: `nav-${item.to}`,
      label: item.label,
      hint: SHORTCUT_BY_PATH[item.to] ?? "Page",
      icon: item.icon,
      onSelect: () => void navigate({ to: item.to }),
    }));
    const objectTypeItems: PaletteItem[] = (objectTypes ?? []).map((ot) => ({
      id: `object-type-${ot.name}`,
      label: ot.name,
      hint: "Object type",
      icon: "cube",
      onSelect: () => void navigate({ to: "/objects/$type", params: { type: ot.name } }),
    }));
    const objectSetItems: PaletteItem[] = objectSets
      .filter((os) => os.visibility !== "hidden")
      .map((os) => {
        const typeName = urnShortName(os.object_type_urn);
        const path = objectSetBrowsePath(typeName, os.name);
        return {
          id: `object-set-${os.name}`,
          label: os.display_name || os.name,
          hint: `Object set · ${typeName}`,
          icon: "filter" as IconName,
          onSelect: () => void navigate({ to: path.to, params: path.params, search: path.search }),
        };
      });
    const applicationItems: PaletteItem[] = (applications ?? []).map((app) => ({
      id: `application-${app.name}`,
      label: app.name,
      hint: "Application",
      icon: "application",
      onSelect: () => void navigate({ to: "/applications/$name", params: { name: app.name } }),
    }));
    const actionItems: PaletteItem[] = ACTIONS.map((action) => ({
      id: action.id,
      label: action.label,
      hint: "Action",
      icon: action.icon,
      onSelect: () => {
        triggerIntent(action.intent);
        void navigate({ to: action.to });
      },
    }));
    return [...navItems, ...actionItems, ...objectTypeItems, ...objectSetItems, ...applicationItems];
  }, [objectTypes, objectSets, applications, navigate, triggerIntent]);

  function handleItemSelect(item: PaletteItem) {
    item.onSelect();
    onClose();
  }

  return (
    <PaletteOmnibar
      isOpen={isOpen}
      onClose={onClose}
      items={items}
      itemsEqual="id"
      resetOnSelect
      inputProps={{ placeholder: "Jump to a page, run an action, search…", leftIcon: "search" }}
      itemPredicate={(query, item) => item.label.toLowerCase().includes(query.toLowerCase())}
      itemRenderer={(item, { handleClick, handleFocus, modifiers }) => {
        if (!modifiers.matchesPredicate) return null;
        return (
          <MenuItem
            key={item.id}
            roleStructure="listoption"
            icon={item.icon}
            text={item.label}
            label={item.hint}
            active={modifiers.active}
            onClick={handleClick}
            onFocus={handleFocus}
          />
        );
      }}
      onItemSelect={handleItemSelect}
      noResults={<MenuItem disabled text="No matches." roleStructure="listoption" />}
      createNewItemFromQuery={(query) => ({
        id: "search-fallback",
        label: `Search for "${query}"`,
        hint: "Search",
        icon: "search",
        onSelect: () => void navigate({ to: "/search", search: { q: query } }),
      })}
      createNewItemRenderer={(query, active, handleClick) => (
        <MenuItem
          key="search-fallback"
          roleStructure="listoption"
          icon="search"
          text={`Search for "${query}"`}
          active={active}
          onClick={handleClick}
        />
      )}
    />
  );
}
