export const IDENTITY_URL = "/api/identity";
export const CONNECTIVITY_URL = "/api/connectivity";
export const KNOWLEDGE_URL = "/api/knowledge";
export const EXPERIENCE_URL = "";
export const INTELLIGENCE_URL = "/api/intelligence";

export const TENANT_ID = import.meta.env.VITE_TENANT_ID ?? "acme";
export const WORKSPACE_ID = import.meta.env.VITE_WORKSPACE_ID ?? "main";

const configuredTimeout = Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 15_000);
export const API_TIMEOUT_MS = Number.isFinite(configuredTimeout) && configuredTimeout > 0 ? configuredTimeout : 15_000;
