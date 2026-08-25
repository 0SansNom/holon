import React, { type ComponentType, type ReactNode } from "react";
import { RouteBoundary } from "../components/common/RouteBoundary";
import { RegistryPageSkeleton } from "../components/common/Skeleton";
import { clearStaleChunkReloadFlag, reloadOnceForStaleChunk } from "./staleChunk";

export function lazyPage(
  factory: () => Promise<Record<string, ComponentType<object>>>,
  exportName: string,
  fallback: ReactNode = <RegistryPageSkeleton />,
): () => React.JSX.Element {
  const LazyComp = React.lazy(async () => {
    try {
      const mod = await factory();
      clearStaleChunkReloadFlag();
      return { default: mod[exportName] };
    } catch (err) {
      if (reloadOnceForStaleChunk(err)) {
        return new Promise(() => {
          /* page is reloading */
        });
      }
      throw err;
    }
  });

  return function LazyWrapper() {
    return (
      <RouteBoundary fallback={fallback}>
        <LazyComp />
      </RouteBoundary>
    );
  };
}
