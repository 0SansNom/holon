import { Suspense, useState } from "react";
import { Tab, Tabs, type TabId } from "@blueprintjs/core";
import { PrincipalsTab } from "./PrincipalsTab";
import { ProjectsTab } from "./ProjectsTab";
import { TenantsTab, WorkspacesTab } from "./TenantsTab";
import { usePaletteIntentStore } from "../../store/paletteIntent";
import { RegistryPage } from "../common/PageLayout";
import { RegistryTabSkeleton } from "../common/Skeleton";

const TAB_SKELETON = <RegistryTabSkeleton />;

export function AdminPage() {
  const intent = usePaletteIntentStore((s) => s.intent);
  const [selectedTabId, setSelectedTabId] = useState<TabId>(() =>
    intent === "create-project" ? "projects" : "principals",
  );

  return (
    <RegistryPage
      title="Admin"
      description={
        <>
          Tenant (filiale), workspace, principal, and project access management — the same governance actions the CLI
          wraps, here as a self-service UI.
        </>
      }
    >
      <Tabs id="admin-tabs" selectedTabId={selectedTabId} onChange={setSelectedTabId} renderActiveTabPanelOnly>
        <Tab id="tenants" title="Tenants" panel={<Suspense fallback={TAB_SKELETON}><TenantsTab /></Suspense>} />
        <Tab
          id="workspaces"
          title="Workspaces"
          panel={
            <Suspense fallback={TAB_SKELETON}>
              <WorkspacesTab />
            </Suspense>
          }
        />
        <Tab
          id="principals"
          title="Principals"
          panel={
            <Suspense fallback={TAB_SKELETON}>
              <PrincipalsTab />
            </Suspense>
          }
        />
        <Tab
          id="projects"
          title="Projects"
          panel={
            <Suspense fallback={TAB_SKELETON}>
              <ProjectsTab />
            </Suspense>
          }
        />
      </Tabs>
    </RegistryPage>
  );
}
