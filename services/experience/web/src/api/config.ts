// Same convention as this project's own Python test suite (tests/*.py):
// fixed localhost dev ports, no env-var indirection for a single-tenant
// dev deployment. The browser is just another authenticated API client —
// it calls Identity/Knowledge directly (CORS enabled, see
// holon_common.instrument_cors) rather than routing every read through a
// hand-duplicated Experience proxy endpoint.
export const IDENTITY_URL = import.meta.env.VITE_IDENTITY_URL ?? "http://localhost:8001";
export const KNOWLEDGE_URL = import.meta.env.VITE_KNOWLEDGE_URL ?? "http://localhost:8003";
export const EXPERIENCE_URL = import.meta.env.VITE_EXPERIENCE_URL ?? "http://localhost:8004";

export const TENANT_ID = "acme";
export const WORKSPACE_ID = "demo";
