import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { IdentityPrincipal } from "../api/identity";

export interface Session {
  // The full principal as Identity's `/whoami` returns it — never
  // login-time client-side claims. `type`/`country`/`on_behalf_of` are
  // what the ABAC/masking layer decides on, so the UI should read them
  // from here rather than re-deriving them from seeded demo data.
  //
  // Deliberately no `token` field — the session JWT lives only in the
  // `holon_session` HttpOnly cookie `POST /login` sets, never in JS-
  // reachable storage. This `principal` is not sensitive (no secret,
  // just who's signed in) and persisting it is purely so the UI can
  // render "signed in as..." across a reload without waiting on
  // `/whoami`; it is not itself proof of a valid session — the cookie
  // is, and `api/client.ts`'s existing 401 handling already covers a
  // missing/expired one.
  principal: IdentityPrincipal;
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
    {
      name: "holon-session",
      // v1 stored {principalUrn, displayName, token}; v2 added the full
      // principal but kept `token`. v3 drops `token` entirely — the
      // session moved to an HttpOnly cookie, so any `token` string still
      // sitting in an existing v2 entry is exactly the localStorage
      // exposure this change closes, not something to carry forward.
      // Old sessions are dropped and the user signs in again — once.
      version: 3,
      migrate: (persisted, version) => {
        if (version < 3) return { session: null };
        return persisted as AuthState;
      },
    },
  ),
);
