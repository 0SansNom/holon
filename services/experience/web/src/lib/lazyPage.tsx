import React, { type ComponentType, type ReactNode } from "react";
import { RouteBoundary } from "../components/common/RouteBoundary";
import { RegistryPageSkeleton } from "../components/common/Skeleton";

export function lazyPage(
  factory: () => Promise<Record<string, ComponentType<object>>>,
  exportName: string,
  fallback: ReactNode = <RegistryPageSkeleton />,
): () => React.JSX.Element {
  const LazyComp = React.lazy(async () => {
    const mod = await factory();
    return { default: mod[exportName] };
  });

  return function LazyWrapper() {
    return (
      <RouteBoundary fallback={fallback}>
        <LazyComp />
      </RouteBoundary>
    );
  };
}
