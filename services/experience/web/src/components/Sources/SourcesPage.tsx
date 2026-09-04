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
    intent === "create-connection" || intent === "create-sql-connection" || intent === "create-object-connection"
      ? "connections"
      : "data-sources",
  );

  return (
    <RegistryPage
      title="Sources"
      description="Connect REST, SQL, files, Kafka, and plugins. Manage credentials and map synced data to object types."
    >
      <Tabs id="sources-tabs" selectedTabId={selectedTabId} onChange={setSelectedTabId} renderActiveTabPanelOnly>
        <Tab id="data-sources" title="Data Sources" panel={<Suspense fallback={TAB_SKELETON}><DataSourcesTab /></Suspense>} />
        <Tab id="connections" title="Connections" panel={<Suspense fallback={TAB_SKELETON}><ConnectionsTab /></Suspense>} />
        <Tab id="write-targets" title="Writeback" panel={<Suspense fallback={TAB_SKELETON}><WriteTargetsTab /></Suspense>} />
      </Tabs>
    </RegistryPage>
  );
}

