export const STALE_CHUNK_RELOAD_KEY = "holon:stale-chunk-reload";

export function isStaleChunkError(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? "");
  return /Failed to fetch dynamically imported module|Loading chunk [\w.-]+ failed|error loading dynamically imported module|Importing a module script failed|Expected a JavaScript module/i.test(
    msg,
  );
}

export function clearStaleChunkReloadFlag(): void {
  try {
    sessionStorage.removeItem(STALE_CHUNK_RELOAD_KEY);
  } catch {
    /* private mode */
  }
}

/** Reload once after a Vite rebuild deleted a lazy chunk. Returns true if a reload was triggered. */
export function reloadOnceForStaleChunk(
  err: unknown,
  reload: () => void = () => window.location.reload(),
): boolean {
  if (!isStaleChunkError(err)) return false;
  try {
    if (sessionStorage.getItem(STALE_CHUNK_RELOAD_KEY) === "1") return false;
    sessionStorage.setItem(STALE_CHUNK_RELOAD_KEY, "1");
  } catch {
    reload();
    return true;
  }
  reload();
  return true;
}
