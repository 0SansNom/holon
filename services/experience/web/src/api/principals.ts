import { TENANT_ID } from "./config";

// The seeded demo principals surfaced here so switching between them
// is the fastest way to actually see the permission model (ReBAC + ABAC + masking) work.
export interface SeededPrincipal {
  localName: string;
  urn: string;
  displayName: string;
  description: string;
}

function urn(localName: string): string {
  return `hl:${TENANT_ID}:global:user:${localName}`;
}

export const SEEDED_PRINCIPALS: SeededPrincipal[] = [
  {
    localName: "jdoe",
    urn: urn("jdoe"),
    displayName: "Jane Doe",
    description: "Workspace editor, France — full ReBAC + ABAC access.",
  },
  {
    localName: "msmith",
    urn: urn("msmith"),
    displayName: "M. Smith",
    description: "Compliance Officer, workspace admin — can approve high-risk Actions and publish ontology versions.",
  },
  {
    localName: "kenji",
    urn: urn("kenji"),
    displayName: "Kenji",
    description: "Workspace viewer, Japan — ReBAC-granted but ABAC-restricted: confidential fields are masked, not the whole read.",
  },
  {
    localName: "alice",
    urn: urn("alice"),
    displayName: "Alice",
    description: "Tenant member only, United States — no workspace access at all (ReBAC-denied).",
  },
];

export function clientSecretFor(localName: string): string {
  // Deterministic dev-only convention this entire build already uses
  // (identity/app/seed.py's client_secret_for) — never a real credential.
  return `${localName}-dev-secret`;
}
