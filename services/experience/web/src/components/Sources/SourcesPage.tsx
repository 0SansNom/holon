import { Suspense, useState } from "react";
import { Tab, Tabs, type TabId } from "@blueprintjs/core";
import { DataSourcesTab } from "./DataSourcesTab";
import { ConnectionsTab } from "./ConnectionsTab";
import { WriteTargetsTab } from "./WriteTargetsTab";
import { usePaletteIntentStore } from "../../store/paletteIntent";
import { RegistryPage } from "../common/PageLayout";
import { RegistryTabSkeleton } from "../common/Skeleton";

const TAB_SKELETON = <RegistryTabSkeleton cards={3} />;

export function SourcesPage() {
  const intent = usePaletteIntentStore((s) => s.intent);
  const [selectedTabId, setSelectedTabId] = useState<TabId>(() =>
    intent === "create-connection" ? "connections" : "data-sources",
  );

  return (
    <RegistryPage
      title="Data Sources"
      description="Connect REST APIs and connector plugins, manage credentials/schedules, writeback targets, and map synced datasets to ObjectTypes."
    >
      <Tabs id="sources-tabs" selectedTabId={selectedTabId} onChange={setSelectedTabId} renderActiveTabPanelOnly>
        <Tab id="data-sources" title="Data Sources" panel={<Suspense fallback={TAB_SKELETON}><DataSourcesTab /></Suspense>} />
        <Tab id="connections" title="Connections" panel={<Suspense fallback={TAB_SKELETON}><ConnectionsTab /></Suspense>} />
        <Tab id="write-targets" title="Writeback" panel={<Suspense fallback={TAB_SKELETON}><WriteTargetsTab /></Suspense>} />
      </Tabs>
    </RegistryPage>
  );
}

