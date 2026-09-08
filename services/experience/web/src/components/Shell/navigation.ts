// Split out from Shell.tsx: CommandPalette.tsx needs this data too, and
// having Shell.tsx import CommandPalette.tsx while CommandPalette.tsx
// imported these constants back from Shell.tsx was a circular import —
// harmless to `tsc`, but a real runtime TDZ crash ("Cannot access
// 'SEQUENTIAL_SHORTCUTS' before initialization") since whichever module
// evaluates second would read the other's not-yet-initialized const.

export type NavItem = {
  to: "/objects" | "/approvals" | "/sources" | "/pipelines" | "/catalog" | "/ontology" | "/applications" | "/collections" | "/search" | "/glossary" | "/admin";
  icon:
    | "cube"
    | "confirm"
    | "data-connection"
    | "flow-branch"
    | "th-list"
    | "diagram-tree"
    | "application"
    | "layers"
    | "search"
    | "book"
    | "shield";
  label: string;
};

export type NavSection = {
  id: string;
  label: string;
  items: readonly NavItem[];
};

export const NAV_SECTIONS: readonly NavSection[] = [
  {
    id: "explore",
    label: "Explore",
    items: [
      { to: "/objects", icon: "cube", label: "Objects" },
      { to: "/search", icon: "search", label: "Search" },
      { to: "/glossary", icon: "book", label: "Glossary" },
      { to: "/approvals", icon: "confirm", label: "Approvals" },
    ],
  },
  {
    id: "build",
    label: "Build",
    items: [
      { to: "/sources", icon: "data-connection", label: "Sources" },
      { to: "/pipelines", icon: "flow-branch", label: "Pipelines" },
      { to: "/catalog", icon: "th-list", label: "Catalog" },
      { to: "/ontology", icon: "diagram-tree", label: "Ontology" },
      { to: "/applications", icon: "application", label: "Applications" },
      { to: "/collections", icon: "layers", label: "Collections" },
    ],
  },
  {
    id: "govern",
    label: "Govern",
    items: [{ to: "/admin", icon: "shield", label: "Admin" }],
  },
] as const;

export const NAV_ITEMS: readonly NavItem[] = NAV_SECTIONS.flatMap((section) => [...section.items]);

// Linear-style leader-key navigation: "g" then a letter, no palette
// needed. Deliberately covers only the 5 highest-traffic destinations
// — Search/Glossary are already one keystroke away via Cmd/Ctrl+K, not
// worth forcing an awkward second mnemonic letter for.
export const SEQUENTIAL_SHORTCUTS: Record<string, NavItem["to"]> = {
  o: "/objects",
  a: "/approvals",
  s: "/sources",
  i: "/pipelines",
  c: "/catalog",
  n: "/ontology",
  p: "/applications",
  d: "/admin",
};
