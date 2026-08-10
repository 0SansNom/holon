import { Suspense, useState } from "react";
import { Tab, Tabs, type TabId } from "@blueprintjs/core";
import { PrincipalsTab } from "./PrincipalsTab";
import { ProjectsTab } from "./ProjectsTab";
import { usePaletteIntentStore } from "../../store/paletteIntent";
import { RegistryPage } from "../common/PageLayout";
import { RegistryTabSkeleton } from "../common/Skeleton";

const TAB_SKELETON = <RegistryTabSkeleton />;

export function AdminPage() {
  const intent = usePaletteIntentStore((s) => s.intent);
  const [selectedTabId, setSelectedTabId] = useState<TabId>(() => (intent === "create-project" ? "projects" : "principals"));

  return (
    <RegistryPage
      title="Admin"
      description={
        <>
          Principal, workspace, and project access management — the same governance actions the CLI's{" "}
          <code>holon principals</code>/<code>holon workspace</code>/<code>holon projects</code> commands wrap,
          here as a self-service UI.
        </>
      }
    >
      <Tabs id="admin-tabs" selectedTabId={selectedTabId} onChange={setSelectedTabId} renderActiveTabPanelOnly>
        <Tab id="principals" title="Principals" panel={<Suspense fallback={TAB_SKELETON}><PrincipalsTab /></Suspense>} />
        <Tab id="projects" title="Projects" panel={<Suspense fallback={TAB_SKELETON}><ProjectsTab /></Suspense>} />
      </Tabs>
    </RegistryPage>
  );
}
