import { afterEach, describe, expect, it, vi } from "vitest";
import {
  STALE_CHUNK_RELOAD_KEY,
  clearStaleChunkReloadFlag,
  isStaleChunkError,
  reloadOnceForStaleChunk,
} from "./staleChunk";

afterEach(() => {
  sessionStorage.clear();
});

describe("isStaleChunkError", () => {
  it("matches Vite dynamic import failures", () => {
    expect(
      isStaleChunkError(
        new Error("Failed to fetch dynamically imported module: http://localhost:8004/assets/ApplicationPage-qAzUEk4I.js"),
      ),
    ).toBe(true);
  });

  it("ignores unrelated errors", () => {
    expect(isStaleChunkError(new Error("Missing queryFn"))).toBe(false);
  });
});

describe("reloadOnceForStaleChunk", () => {
  it("reloads once then refuses", () => {
    const reload = vi.fn();
    const err = new Error("Failed to fetch dynamically imported module: /assets/x.js");
    expect(reloadOnceForStaleChunk(err, reload)).toBe(true);
    expect(reload).toHaveBeenCalledOnce();
    expect(sessionStorage.getItem(STALE_CHUNK_RELOAD_KEY)).toBe("1");
    expect(reloadOnceForStaleChunk(err, reload)).toBe(false);
    expect(reload).toHaveBeenCalledOnce();
  });

  it("clears the flag after a successful load", () => {
    sessionStorage.setItem(STALE_CHUNK_RELOAD_KEY, "1");
    clearStaleChunkReloadFlag();
    expect(sessionStorage.getItem(STALE_CHUNK_RELOAD_KEY)).toBeNull();
  });
});
