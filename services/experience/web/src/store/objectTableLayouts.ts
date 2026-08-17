import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  DEFAULT_COLUMN_LAYOUT,
  normalizeColumnLayout,
  type ObjectTableColumnLayout,
} from "../components/ObjectExplorer/columnLayout";

type ObjectTableLayoutsState = {
  byType: Record<string, ObjectTableColumnLayout>;
  getLayout: (objectType: string) => ObjectTableColumnLayout;
  setLayout: (objectType: string, layout: ObjectTableColumnLayout) => void;
  resetLayout: (objectType: string) => void;
};

export const useObjectTableLayoutsStore = create<ObjectTableLayoutsState>()(
  persist(
    (set, get) => ({
      byType: {},
      getLayout: (objectType) => normalizeColumnLayout(get().byType[objectType] ?? DEFAULT_COLUMN_LAYOUT),
      setLayout: (objectType, layout) =>
        set((state) => ({
          byType: { ...state.byType, [objectType]: normalizeColumnLayout(layout) },
        })),
      resetLayout: (objectType) =>
        set((state) => {
          const next = { ...state.byType };
          delete next[objectType];
          return { byType: next };
        }),
    }),
    {
      name: "hl-oe-column-layouts",
      version: 1,
      partialize: (state) => ({ byType: state.byType }),
    },
  ),
);
