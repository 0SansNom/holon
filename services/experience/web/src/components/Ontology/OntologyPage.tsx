import { Suspense, useState, useTransition } from "react";
import { Tab, Tabs, type TabId } from "@blueprintjs/core";
import { ObjectTypesTab } from "./ObjectTypesTab";
import { InterfacesTab } from "./InterfacesTab";
import { RelationTypesTab } from "./RelationTypesTab";
import { ValueTypesTab } from "./ValueTypesTab";
import { SharedPropertyTypesTab } from "./SharedPropertyTypesTab";
import { ActionTypesTab } from "./ActionTypesTab";
import { ObjectTypeGroupsTab } from "./ObjectTypeGroupsTab";
import { HealthCheckTab } from "./HealthCheckTab";
import { usePaletteIntentStore } from "../../store/paletteIntent";
import { RegistryPage } from "../common/PageLayout";
import { RegistryTabSkeleton } from "../common/Skeleton";

const TAB_BY_INTENT: Partial<Record<string, TabId>> = {
  "create-interface": "interfaces",
  "create-relation-type": "relation-types",
  "create-value-type": "value-types",
  "create-shared-property-type": "shared-property-types",
  "create-action-type": "action-types",
  "create-object-type-group": "object-type-groups",
};

const TAB_SKELETON = <RegistryTabSkeleton />;

export function OntologyPage() {
  const intent = usePaletteIntentStore((s) => s.intent);
  const [selectedTabId, setSelectedTabId] = useState<TabId>(() => TAB_BY_INTENT[intent ?? ""] ?? "object-types");
  const [isPending, startTransition] = useTransition();

  function selectTab(tabId: TabId) {
    startTransition(() => setSelectedTabId(tabId));
  }

  return (
    <RegistryPage
      title="Ontology"
      description={
        <>
          Define ObjectTypes, Interfaces, RelationTypes, Value Types, Shared Property Types, and Action Types — no
          code required. The same governance actions the CLI's <code>holon ontology</code> commands and the raw API
          already wrap.
        </>
      }
    >
      <Tabs id="ontology-tabs" selectedTabId={selectedTabId} onChange={selectTab} renderActiveTabPanelOnly>
        <Tab id="object-types" title="ObjectTypes" panel={<Suspense fallback={TAB_SKELETON}><ObjectTypesTab /></Suspense>} />
        <Tab id="interfaces" title="Interfaces" panel={<Suspense fallback={TAB_SKELETON}><InterfacesTab /></Suspense>} />
        <Tab id="relation-types" title="RelationTypes" panel={<Suspense fallback={TAB_SKELETON}><RelationTypesTab /></Suspense>} />
        <Tab id="value-types" title="Value Types" panel={<Suspense fallback={TAB_SKELETON}><ValueTypesTab /></Suspense>} />
        <Tab id="shared-property-types" title="Shared Property Types" panel={<Suspense fallback={TAB_SKELETON}><SharedPropertyTypesTab /></Suspense>} />
        <Tab id="action-types" title="Action Types" panel={<Suspense fallback={TAB_SKELETON}><ActionTypesTab /></Suspense>} />
        <Tab id="object-type-groups" title="Object Type Groups" panel={<Suspense fallback={TAB_SKELETON}><ObjectTypeGroupsTab /></Suspense>} />
        <Tab id="health-check" title="Health Check" panel={<HealthCheckTab />} />
      </Tabs>
      {isPending && <div className="hl-mt-sm hl-skeleton" style={{ width: 120, height: 16 }} aria-hidden />}
    </RegistryPage>
  );
}
