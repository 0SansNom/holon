import { create } from "zustand";
import { persist } from "zustand/middleware";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

function resolveTheme(preference: ThemePreference): ResolvedTheme {
  if (preference === "system") {
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  return preference;
}

function applyTheme(resolved: ResolvedTheme) {
  const root = document.documentElement;
  root.dataset.theme = resolved;
  root.style.colorScheme = resolved;
  root.classList.toggle("bp6-dark", resolved === "dark");
}

interface ThemeState {
  preference: ThemePreference;
  resolved: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
  syncSystem: () => void;
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set, get) => ({
      preference: "system",
      resolved: resolveTheme("system"),
      setPreference: (preference) => {
        const resolved = resolveTheme(preference);
        applyTheme(resolved);
        set({ preference, resolved });
      },
      syncSystem: () => {
        if (get().preference !== "system") return;
        const resolved = resolveTheme("system");
        applyTheme(resolved);
        set({ resolved });
      },
    }),
    {
      name: "holon-theme",
      partialize: (state) => ({ preference: state.preference }),
      onRehydrateStorage: () => (state) => {
        const preference = state?.preference ?? "system";
        const resolved = resolveTheme(preference);
        applyTheme(resolved);
        useThemeStore.setState({ preference, resolved });
      },
    },
  ),
);

export function initTheme() {
  const { preference, resolved } = useThemeStore.getState();
  applyTheme(resolved);
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    useThemeStore.getState().syncSystem();
  });
  if (preference !== useThemeStore.getState().preference) {
    applyTheme(useThemeStore.getState().resolved);
  }
}
