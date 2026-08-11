import React, { type ComponentType, type ReactNode } from "react";
import { createRootRoute, createRoute, createRouter, Outlet, redirect } from "@tanstack/react-router";
import { Shell } from "./components/Shell/Shell";
import { LoginScreen } from "./components/Auth/LoginScreen";
import { RouteBoundary } from "./components/common/RouteBoundary";
import { DetailPageSkeleton, RegistryPageSkeleton, TablePageSkeleton } from "./components/common/Skeleton";
import { useAuthStore } from "./store/auth";

function lazyPage(
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

const ObjectTypeListPage = lazyPage(() => import("./components/ObjectExplorer/ObjectTypeListPage"), "ObjectTypeListPage");
const ObjectTablePage = lazyPage(
  () => import("./components/ObjectExplorer/ObjectTablePage"),
  "ObjectTablePage",
  <TablePageSkeleton />,
);
const ObjectDetailPage = lazyPage(
  () => import("./components/ObjectExplorer/ObjectDetailPage"),
  "ObjectDetailPage",
  <DetailPageSkeleton />,
);
const ObjectGraphPage = lazyPage(
  () => import("./components/ObjectExplorer/ObjectGraphPage"),
  "ObjectGraphPage",
  <DetailPageSkeleton />,
);
const ApplicationListPage = lazyPage(() => import("./components/Applications/ApplicationListPage"), "ApplicationListPage");
const ApplicationPage = lazyPage(
  () => import("./components/Applications/ApplicationPage"),
  "ApplicationPage",
  <DetailPageSkeleton />,
);
const LineagePage = lazyPage(
  () => import("./components/Lineage/LineagePage"),
  "LineagePage",
  <DetailPageSkeleton />,
);
const SearchPage = lazyPage(() => import("./components/Search/SearchPage"), "SearchPage");
const GlossaryPage = lazyPage(() => import("./components/Glossary/GlossaryPage"), "GlossaryPage");
const AdminPage = lazyPage(() => import("./components/Admin/AdminPage"), "AdminPage");
const ProjectDetailPage = lazyPage(
  () => import("./components/Admin/ProjectDetailPage"),
  "ProjectDetailPage",
  <DetailPageSkeleton />,
);
const CollectionListPage = lazyPage(() => import("./components/Collections/CollectionListPage"), "CollectionListPage");
const CollectionDetailPage = lazyPage(
  () => import("./components/Collections/CollectionDetailPage"),
  "CollectionDetailPage",
  <DetailPageSkeleton />,
);
const SourcesPage = lazyPage(() => import("./components/Sources/SourcesPage"), "SourcesPage");
const OntologyPage = lazyPage(() => import("./components/Ontology/OntologyPage"), "OntologyPage");

const rootRoute = createRootRoute({
  component: () => <Outlet />,
});

const loginRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/login",
  component: LoginScreen,
});

const shellRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: "shell",
  component: Shell,
  beforeLoad: () => {
    if (!useAuthStore.getState().session) {
      throw redirect({ to: "/login" });
    }
  },
});

const objectTypesRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/objects",
  component: ObjectTypeListPage,
});

const objectTableRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/objects/$type",
  component: ObjectTablePage,
  validateSearch: (search: Record<string, unknown>): { set?: string } => ({
    set: typeof search.set === "string" && search.set.length > 0 ? search.set : undefined,
  }),
});

const objectDetailRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/objects/$type/$id",
  component: ObjectDetailPage,
});

const objectGraphRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/objects/$type/$id/graph",
  component: ObjectGraphPage,
});

const applicationsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/applications",
  component: ApplicationListPage,
});

const applicationRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/applications/$name",
  component: ApplicationPage,
});

const collectionsRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/collections",
  component: CollectionListPage,
});

const collectionDetailRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/collections/$id",
  component: CollectionDetailPage,
});

const lineageRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/lineage/$urn",
  component: LineagePage,
});

const searchRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/search",
  component: SearchPage,
  validateSearch: (search: Record<string, unknown>): { q?: string } => ({
    q: typeof search.q === "string" ? search.q : undefined,
  }),
});

const glossaryRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/glossary",
  component: GlossaryPage,
});

const adminRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/admin",
  component: AdminPage,
});

const projectDetailRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/admin/projects/$name",
  component: ProjectDetailPage,
});

const sourcesRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/sources",
  component: SourcesPage,
});

const ontologyRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/ontology",
  component: OntologyPage,
});

const indexRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/",
  component: ObjectTypeListPage,
});

const routeTree = rootRoute.addChildren([
  loginRoute,
  shellRoute.addChildren([
    indexRoute,
    objectTypesRoute,
    objectTableRoute,
    objectDetailRoute,
    objectGraphRoute,
    applicationsRoute,
    applicationRoute,
    collectionsRoute,
    collectionDetailRoute,
    lineageRoute,
    searchRoute,
    glossaryRoute,
    adminRoute,
    projectDetailRoute,
    sourcesRoute,
    ontologyRoute,
  ]),
]);

export const router = createRouter({ routeTree });

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
