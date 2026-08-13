import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  normalizeExploration,
  normalizeObjectList,
  type SavedExploration,
  type SavedObjectList,
} from "../components/ObjectExplorer/savedViews";

type ObjectExplorerViewsState = {
  explorations: SavedExploration[];
  lists: SavedObjectList[];
  upsertExploration: (exploration: SavedExploration) => void;
  deleteExploration: (id: string) => void;
  upsertList: (list: SavedObjectList) => void;
  deleteList: (id: string) => void;
  explorationsForType: (objectType: string) => SavedExploration[];
  listsForType: (objectType: string) => SavedObjectList[];
  getExploration: (id: string) => SavedExploration | undefined;
  getList: (id: string) => SavedObjectList | undefined;
};

export const useObjectExplorerViewsStore = create<ObjectExplorerViewsState>()(
  persist(
    (set, get) => ({
      explorations: [],
      lists: [],
      upsertExploration: (exploration) => {
        const normalized = normalizeExploration(exploration);
        if (!normalized) return;
        set((state) => {
          const without = state.explorations.filter((e) => e.id !== normalized.id);
          return { explorations: [normalized, ...without] };
        });
      },
      deleteExploration: (id) =>
        set((state) => ({ explorations: state.explorations.filter((e) => e.id !== id) })),
      upsertList: (list) => {
        const normalized = normalizeObjectList(list);
        if (!normalized) return;
        set((state) => {
          const without = state.lists.filter((l) => l.id !== normalized.id);
          return { lists: [normalized, ...without] };
        });
      },
      deleteList: (id) => set((state) => ({ lists: state.lists.filter((l) => l.id !== id) })),
      explorationsForType: (objectType) => get().explorations.filter((e) => e.objectType === objectType),
      listsForType: (objectType) => get().lists.filter((l) => l.objectType === objectType),
      getExploration: (id) => get().explorations.find((e) => e.id === id),
      getList: (id) => get().lists.find((l) => l.id === id),
    }),
    {
      name: "hl-oe-saved-views",
      version: 1,
      partialize: (state) => ({ explorations: state.explorations, lists: state.lists }),
      migrate: (persisted) => {
        const p = persisted as { explorations?: unknown[]; lists?: unknown[] };
        return {
          explorations: (p.explorations ?? [])
            .map((e) => normalizeExploration(e as Partial<SavedExploration>))
            .filter((e): e is SavedExploration => e != null),
          lists: (p.lists ?? [])
            .map((l) => normalizeObjectList(l as Partial<SavedObjectList>))
            .filter((l): l is SavedObjectList => l != null),
        };
      },
    },
  ),
);
