import { create } from "zustand";
import { persist } from "zustand/middleware";

export interface Session {
  principalUrn: string;
  displayName: string;
  token: string;
}

interface AuthState {
  session: Session | null;
  setSession: (session: Session) => void;
  clear: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      session: null,
      setSession: (session) => set({ session }),
      clear: () => set({ session: null }),
    }),
    { name: "holon-session" },
  ),
);

export function authHeader(): Record<string, string> {
  const token = useAuthStore.getState().session?.token;
  return token ? { Authorization: `Bearer ${token}` } : {};
}
