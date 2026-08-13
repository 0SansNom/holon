import { create } from "zustand";
import { persist } from "zustand/middleware";
import {
  defaultObjectViewDefinition,
  normalizeObjectViewDefinition,
  type ObjectViewDefinition,
  type ObjectViewMode,
} from "../components/ObjectExplorer/objectViewDefinition";

type ObjectViewDefinitionsState = {
  byType: Record<string, ObjectViewDefinition>;
  preferredModeByType: Record<string, ObjectViewMode>;
  getDefinition: (objectType: string) => ObjectViewDefinition | undefined;
  hasConfigured: (objectType: string) => boolean;
  upsertDefinition: (definition: ObjectViewDefinition) => void;
  ensureDefault: (objectType: string) => ObjectViewDefinition;
  deleteDefinition: (objectType: string) => void;
  getPreferredMode: (objectType: string) => ObjectViewMode | undefined;
  setPreferredMode: (objectType: string, mode: ObjectViewMode) => void;
};

export const useObjectViewDefinitionsStore = create<ObjectViewDefinitionsState>()(
  persist(
    (set, get) => ({
      byType: {},
      preferredModeByType: {},
      getDefinition: (objectType) => {
        const raw = get().byType[objectType];
        return raw ? (normalizeObjectViewDefinition(raw, objectType) ?? undefined) : undefined;
      },
      hasConfigured: (objectType) => get().getDefinition(objectType) != null,
      upsertDefinition: (definition) => {
        const normalized = normalizeObjectViewDefinition({ ...definition, updatedAt: Date.now() });
        if (!normalized) return;
        set((state) => ({
          byType: { ...state.byType, [normalized.objectType]: normalized },
          preferredModeByType: {
            ...state.preferredModeByType,
            [normalized.objectType]: state.preferredModeByType[normalized.objectType] ?? "configured",
          },
        }));
      },
      ensureDefault: (objectType) => {
        const existing = get().getDefinition(objectType);
        if (existing) return existing;
        const created = defaultObjectViewDefinition(objectType);
        get().upsertDefinition(created);
        return created;
      },
      deleteDefinition: (objectType) =>
        set((state) => {
          const byType = { ...state.byType };
          delete byType[objectType];
          const preferredModeByType = { ...state.preferredModeByType };
          delete preferredModeByType[objectType];
          return { byType, preferredModeByType };
        }),
      getPreferredMode: (objectType) => get().preferredModeByType[objectType],
      setPreferredMode: (objectType, mode) =>
        set((state) => ({
          preferredModeByType: { ...state.preferredModeByType, [objectType]: mode },
        })),
    }),
    {
      name: "hl-ov-definitions",
      version: 1,
      partialize: (state) => ({
        byType: state.byType,
        preferredModeByType: state.preferredModeByType,
      }),
      migrate: (persisted) => {
        const p = persisted as {
          byType?: Record<string, unknown>;
          preferredModeByType?: Record<string, unknown>;
        };
        const byType: Record<string, ObjectViewDefinition> = {};
        for (const [key, value] of Object.entries(p.byType ?? {})) {
          const normalized = normalizeObjectViewDefinition(value as Partial<ObjectViewDefinition>, key);
          if (normalized) byType[key] = normalized;
        }
        const preferredModeByType: Record<string, ObjectViewMode> = {};
        for (const [key, value] of Object.entries(p.preferredModeByType ?? {})) {
          if (value === "standard" || value === "configured") preferredModeByType[key] = value;
        }
        return { byType, preferredModeByType };
      },
    },
  ),
);
