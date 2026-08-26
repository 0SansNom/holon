import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { useOptionalSuspenseQuery } from "./optionalSuspenseQuery";

function wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

describe("useOptionalSuspenseQuery", () => {
  it("does not fetch or throw when disabled", () => {
    const queryFn = vi.fn(async () => ["row"]);
    const { result } = renderHook(
      () => useOptionalSuspenseQuery(false, ["objects", ""], queryFn),
      { wrapper },
    );
    expect(result.current.data).toBeUndefined();
    expect(queryFn).not.toHaveBeenCalled();
  });

  it("fetches when enabled", async () => {
    const { result } = renderHook(
      () => useOptionalSuspenseQuery(true, ["objects", "Customer"], async () => [{ id: 1 }]),
      { wrapper },
    );
    await waitFor(() => expect(result.current.data).toEqual([{ id: 1 }]));
  });
});
