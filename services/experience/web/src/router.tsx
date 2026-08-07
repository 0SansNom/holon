import { createRootRoute, createRoute, createRouter, Outlet } from "@tanstack/react-router";
import { Shell } from "./components/Shell/Shell";
import { LoginScreen } from "./components/Auth/LoginScreen";
import { ObjectTypeListPage } from "./components/ObjectExplorer/ObjectTypeListPage";
import { ObjectTablePage } from "./components/ObjectExplorer/ObjectTablePage";
import { ObjectDetailPage } from "./components/ObjectExplorer/ObjectDetailPage";
import { ObjectGraphPage } from "./components/ObjectExplorer/ObjectGraphPage";
import { ApplicationListPage } from "./components/Applications/ApplicationListPage";
import { ApplicationPage } from "./components/Applications/ApplicationPage";
import { LineagePage } from "./components/Lineage/LineagePage";
import { SearchPage } from "./components/Search/SearchPage";
import { GlossaryPage } from "./components/Glossary/GlossaryPage";
import { AdminPage } from "./components/Admin/AdminPage";
import { SourcesPage } from "./components/Sources/SourcesPage";
import { OntologyPage } from "./components/Ontology/OntologyPage";

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

const lineageRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/lineage/$urn",
  component: LineagePage,
});

const searchRoute = createRoute({
  getParentRoute: () => shellRoute,
  path: "/search",
  component: SearchPage,
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
    lineageRoute,
    searchRoute,
    glossaryRoute,
    adminRoute,
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
