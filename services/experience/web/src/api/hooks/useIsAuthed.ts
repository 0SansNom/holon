import { useAuthStore } from "../../store/auth";

export function useIsAuthed(): boolean {
  return useAuthStore((s) => s.session !== null);
}
