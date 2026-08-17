import { useAuthStore } from "../store/auth";
import { redirectToLogin } from "./authRedirect";

export type HolonErrorBody = {
  detail: string;
  error_code?: string;
  error_name?: string;
  error_instance_id?: string;
  parameters?: Record<string, unknown>;
  service?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function detailFromBody(body: unknown): string {
  if (isRecord(body) && "detail" in body) {
    const detail = body.detail;
    if (typeof detail === "string") {
      return detail;
    }
  }
  return String(body);
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  errorCode?: string;
  errorName?: string;
  errorInstanceId?: string;
  parameters: Record<string, unknown>;
  service?: string;

  constructor(status: number, body: unknown) {
    super(detailFromBody(body));
    this.status = status;
    this.body = body;
    this.parameters = {};
    if (isRecord(body)) {
      if (typeof body.errorCode === "string") {
        this.errorCode = body.errorCode;
      }
      if (typeof body.errorName === "string") {
        this.errorName = body.errorName;
      }
      if (typeof body.errorInstanceId === "string") {
        this.errorInstanceId = body.errorInstanceId;
      }
      if (typeof body.service === "string") {
        this.service = body.service;
      }
      if (isRecord(body.parameters)) {
        this.parameters = body.parameters;
      }
    }
  }
}

function handleUnauthorized() {
  // A 401 means the session cookie is expired, missing, or invalid: the
  // session is unusable whatever the UI does next, so drop the locally-
  // held principal and bounce to the login screen centrally instead of
  // letting every query fail its own way. Skipped on the login screen
  // itself — a failed sign-in (bad secret on POST /login) is reported
  // inline there, not by reloading.
  useAuthStore.getState().clear();
  if (!window.location.pathname.startsWith("/login")) {
    redirectToLogin();
  }
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  // The session lives in an HttpOnly cookie now, not a token this code
  // ever holds — `credentials: "include"` is what makes the browser
  // attach it (and accept `Set-Cookie` from `/login`) across the 5
  // different service ports this SPA calls directly.
  const response = await fetch(url, {
    method,
    headers,
    credentials: "include",
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const contentType = response.headers.get("content-type") ?? "";
  const parsed = contentType.includes("application/json") ? await response.json() : await response.text();
  if (response.status === 401) {
    handleUnauthorized();
  }
  if (!response.ok) {
    throw new ApiError(response.status, parsed);
  }
  return parsed as T;
}

export const api = {
  get: <T>(url: string) => request<T>("GET", url),
  post: <T>(url: string, body?: unknown) => request<T>("POST", url, body),
  put: <T>(url: string, body?: unknown) => request<T>("PUT", url, body ?? {}),
  delete: <T>(url: string) => request<T>("DELETE", url),
};

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    return error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === "string") {
    return error;
  }
  return "An unexpected error occurred";
}
