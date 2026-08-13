import { create } from "zustand";
import { persist } from "zustand/middleware";

type ObjectExplorerFavoritesState = {
  objectTypes: string[];
  toggleObjectType: (name: string) => void;
  isFavorite: (name: string) => boolean;
};

export const useObjectExplorerFavoritesStore = create<ObjectExplorerFavoritesState>()(
  persist(
    (set, get) => ({
      objectTypes: [],
      toggleObjectType: (name) =>
        set((state) => {
          const has = state.objectTypes.includes(name);
          return {
            objectTypes: has
              ? state.objectTypes.filter((n) => n !== name)
              : [...state.objectTypes, name],
          };
        }),
      isFavorite: (name) => get().objectTypes.includes(name),
    }),
    {
      name: "hl-oe-favorites",
      version: 1,
      partialize: (state) => ({ objectTypes: state.objectTypes }),
    },
  ),
);
