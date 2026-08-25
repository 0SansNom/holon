import type { GenericSource } from "../../api/connectivity";

export type AuthMethod = "none" | "connection" | "inline";

export const EMPTY_FORM = {
  name: "",
  baseUrl: "",
  authMethod: "none" as AuthMethod,
  connectionName: "",
  authHeaderName: "",
  authHeaderValue: "",
  recordPath: "",
  nextPagePath: "",
  scheduleIntervalMinutes: "",
  cursorProperty: "",
  incrementalParam: "",
};

export function snakeToCamel(s: string): string {
  return s.replace(/_([a-z0-9])/g, (_, c: string) => c.toUpperCase());
}

export function toPascalCase(s: string): string {
  const camel = snakeToCamel(s);
  return camel.charAt(0).toUpperCase() + camel.slice(1);
}

// Client-side estimate only — the scheduler (`main.py`'s
// `run_scheduler_forever`) is the real source of truth, checking
// roughly once a minute, so this is "about" due, not a countdown to
// trust to the second.
export function nextSyncDescription(lastFinishedAt: string, intervalMinutes: number): string {
  const dueAt = new Date(lastFinishedAt).getTime() + intervalMinutes * 60_000;
  const minutesLeft = Math.round((dueAt - Date.now()) / 60_000);
  if (minutesLeft <= 0) return "due now";
  if (minutesLeft < 60) return `in ~${minutesLeft}min`;
  return `in ~${Math.round(minutesLeft / 60)}h`;
}

export const CLASSIFICATIONS = ["public", "internal", "confidential", "restricted"] as const;

export const SECRET_REF_HELP =
  "e.g. env:ERP_PASSWORD or vault:connectors/<tenant>/db#password — Holon stores the reference, not the secret.";

export function formFromSource(source: GenericSource) {
  const authMethod: AuthMethod = source.connection_name ? "connection" : source.auth_header_name ? "inline" : "none";
  return {
    name: source.name,
    baseUrl: source.base_url,
    authMethod,
    connectionName: source.connection_name ?? "",
    authHeaderName: source.auth_header_name ?? "",
    authHeaderValue: "",
    recordPath: source.record_path ?? "",
    nextPagePath: source.next_page_path ?? "",
    scheduleIntervalMinutes: source.schedule_interval_minutes != null ? String(source.schedule_interval_minutes) : "",
    cursorProperty: source.cursor_property ?? "",
    incrementalParam: source.incremental_param ?? "",
  };
}
