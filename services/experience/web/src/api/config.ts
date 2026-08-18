export const IDENTITY_URL = import.meta.env.VITE_IDENTITY_URL ?? "http://localhost:8001";
export const CONNECTIVITY_URL = import.meta.env.VITE_CONNECTIVITY_URL ?? "http://localhost:8002";
export const KNOWLEDGE_URL = import.meta.env.VITE_KNOWLEDGE_URL ?? "http://localhost:8003";
export const EXPERIENCE_URL = import.meta.env.VITE_EXPERIENCE_URL ?? "http://localhost:8004";
export const INTELLIGENCE_URL = import.meta.env.VITE_INTELLIGENCE_URL ?? "http://localhost:8006";

export const TENANT_ID = import.meta.env.VITE_TENANT_ID ?? "acme";
export const WORKSPACE_ID = import.meta.env.VITE_WORKSPACE_ID ?? "main";

const configuredTimeout = Number(import.meta.env.VITE_API_TIMEOUT_MS ?? 15_000);
export const API_TIMEOUT_MS = Number.isFinite(configuredTimeout) && configuredTimeout > 0 ? configuredTimeout : 15_000;

export function validateConfig(): { isDevFallback: boolean; missingVars: string[] } {
  const missingVars: string[] = [];
  const envKeys = [
    "VITE_IDENTITY_URL",
    "VITE_CONNECTIVITY_URL",
    "VITE_KNOWLEDGE_URL",
    "VITE_EXPERIENCE_URL",
    "VITE_INTELLIGENCE_URL",
    "VITE_TENANT_ID",
    "VITE_WORKSPACE_ID",
  ];

  for (const key of envKeys) {
    if (!import.meta.env[key]) {
      missingVars.push(key);
    }
  }

  const isDevFallback = missingVars.length > 0;
  if (isDevFallback && import.meta.env.PROD) {
    console.error("[Config Warning] Running in production mode with default fallback values for:", missingVars);
  }

  return { isDevFallback, missingVars };
}

validateConfig();
