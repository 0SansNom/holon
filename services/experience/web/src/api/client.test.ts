import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./client";

describe("api client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("sends cookie-authenticated requests and parses JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ name: "Holon" }), {
      headers: { "content-type": "application/json" },
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.get<{ name: string }>("/api/example")).resolves.toEqual({ name: "Holon" });
    expect(fetchMock).toHaveBeenCalledWith("/api/example", expect.objectContaining({
      credentials: "include",
      method: "GET",
      signal: expect.any(AbortSignal),
    }));
  });

  it("returns null for a successful no-content response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api.delete<null>("/api/example")).resolves.toBeNull();
  });

  it("preserves structured API errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "Denied", errorCode: "FORBIDDEN" }), {
      status: 403,
      headers: { "content-type": "application/json" },
    })));

    await expect(api.get("/api/example")).rejects.toMatchObject({
      status: 403,
      errorCode: "FORBIDDEN",
      message: "Denied",
    });
  });

  it("aborts requests that exceed their timeout", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("fetch", vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    })));

    const request = api.get("/api/slow", { timeoutMs: 25 });
    const timedOut = expect(request).rejects.toThrow("Request timed out after 25ms");
    await vi.advanceTimersByTimeAsync(25);
    await timedOut;
    vi.useRealTimers();
  });
});
