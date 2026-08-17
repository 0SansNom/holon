import { create } from "zustand";
import { persist } from "zustand/middleware";

export type OntologyResourceKind =
  | "object_type"
  | "relation_type"
  | "action_type"
  | "interface"
  | "value_type"
  | "shared_property_type";

export type OntologyRecentItem = {
  kind: OntologyResourceKind;
  name: string;
  visitedAt: number;
};

type OntologyDiscoverState = {
  favorites: OntologyRecentItem[];
  recent: OntologyRecentItem[];
  toggleFavorite: (kind: OntologyResourceKind, name: string) => void;
  isFavorite: (kind: OntologyResourceKind, name: string) => boolean;
  recordVisit: (kind: OntologyResourceKind, name: string) => void;
};

function sameItem(a: OntologyRecentItem, b: Pick<OntologyRecentItem, "kind" | "name">) {
  return a.kind === b.kind && a.name === b.name;
}

export const useOntologyDiscoverStore = create<OntologyDiscoverState>()(
  persist(
    (set, get) => ({
      favorites: [],
      recent: [],
      toggleFavorite: (kind, name) =>
        set((state) => {
          const exists = state.favorites.some((f) => sameItem(f, { kind, name }));
          return {
            favorites: exists
              ? state.favorites.filter((f) => !sameItem(f, { kind, name }))
              : [{ kind, name, visitedAt: Date.now() }, ...state.favorites].slice(0, 40),
          };
        }),
      isFavorite: (kind, name) => get().favorites.some((f) => sameItem(f, { kind, name })),
      recordVisit: (kind, name) =>
        set((state) => {
          const next = [
            { kind, name, visitedAt: Date.now() },
            ...state.recent.filter((r) => !sameItem(r, { kind, name })),
          ].slice(0, 24);
          return { recent: next };
        }),
    }),
    {
      name: "hl-om-discover",
      version: 1,
      partialize: (state) => ({ favorites: state.favorites, recent: state.recent }),
    },
  ),
);
