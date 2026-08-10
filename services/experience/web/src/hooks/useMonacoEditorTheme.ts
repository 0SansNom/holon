import { useThemeStore } from "../store/theme";

export function useMonacoEditorTheme() {
  return useThemeStore((s) => (s.resolved === "dark" ? "vs-dark" : "vs-light"));
}
