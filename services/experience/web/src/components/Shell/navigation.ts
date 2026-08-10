// Split out from Shell.tsx: CommandPalette.tsx needs this data too, and
// having Shell.tsx import CommandPalette.tsx while CommandPalette.tsx
// imported these constants back from Shell.tsx was a circular import —
// harmless to `tsc`, but a real runtime TDZ crash ("Cannot access
// 'SEQUENTIAL_SHORTCUTS' before initialization") since whichever module
// evaluates second would read the other's not-yet-initialized const.
export const NAV_ITEMS = [
  { to: "/objects", icon: "cube" as const, label: "Objects" },
  { to: "/sources", icon: "data-connection" as const, label: "Sources" },
  { to: "/ontology", icon: "diagram-tree" as const, label: "Ontology" },
  { to: "/applications", icon: "application" as const, label: "Applications" },
  { to: "/collections", icon: "layers" as const, label: "Collections" },
  { to: "/search", icon: "search" as const, label: "Search" },
  { to: "/glossary", icon: "book" as const, label: "Glossary" },
  { to: "/admin", icon: "shield" as const, label: "Admin" },
] as const;

// Linear-style leader-key navigation: "g" then a letter, no palette
// needed. Deliberately covers only the 5 highest-traffic destinations
// — Search/Glossary are already one keystroke away via Cmd/Ctrl+K, not
// worth forcing an awkward second mnemonic letter for.
export const SEQUENTIAL_SHORTCUTS: Record<string, (typeof NAV_ITEMS)[number]["to"]> = {
  o: "/objects",
  s: "/sources",
  n: "/ontology",
  p: "/applications",
  d: "/admin",
};
