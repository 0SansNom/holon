import { create } from "zustand";

// A command-palette action (e.g. "New Value Type") navigates to the
// owning page, then that page's own tab component reads this intent
// on mount to auto-open its dialog and immediately consume it — no
// URL/search-param plumbing needed for what is an ephemeral trigger,
// not something worth deep-linking or persisting.
export type PaletteIntent =
  | "create-value-type"
  | "create-interface"
  | "create-relation-type"
  | "create-shared-property-type"
  | "create-action-type"
  | "create-object-type-group"
  | "create-object-set"
  | "create-pipeline"
  | "create-project"
  | "connect-source"
  | "create-connection";

interface PaletteIntentState {
  intent: PaletteIntent | null;
  trigger: (intent: PaletteIntent) => void;
  consume: () => void;
}

export const usePaletteIntentStore = create<PaletteIntentState>((set) => ({
  intent: null,
  trigger: (intent) => set({ intent }),
  consume: () => set({ intent: null }),
}));
