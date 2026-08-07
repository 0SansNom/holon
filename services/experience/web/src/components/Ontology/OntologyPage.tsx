import { H3, Tab, Tabs } from "@blueprintjs/core";
import { ObjectTypesTab } from "./ObjectTypesTab";
import { InterfacesTab } from "./InterfacesTab";
import { RelationTypesTab } from "./RelationTypesTab";
import { ValueTypesTab } from "./ValueTypesTab";
import { SharedPropertyTypesTab } from "./SharedPropertyTypesTab";
import { ActionTypesTab } from "./ActionTypesTab";

// No-code ontology definition — every registry rendered here
// (ObjectTypes/Interfaces/RelationTypes/Value Types/Shared Property
// Types/Action Types) has been API/JSON-only since it was built; this
// is the frontend catching up, same framing as the Admin page for
// principal/project governance. Deliberately form-first (Admin's
// list+dialog pattern), not the Application Builder's drag-and-drop
// canvas — these are independent named registries with structured
// fields, not one spatially-composed definition, so there's nothing to
// drag-and-drop reorder here.
export function OntologyPage() {
  return (
    <div>
      <H3>Ontology</H3>
      <p style={{ color: "var(--hl-text-muted)", marginBottom: 20 }}>
        Define ObjectTypes, Interfaces, RelationTypes, Value Types, Shared Property Types, and Action Types — no
        code required. The same governance actions the CLI's <code>holon ontology</code> commands and the raw API
        already wrap.
      </p>
      <Tabs id="ontology-tabs" renderActiveTabPanelOnly>
        <Tab id="object-types" title="ObjectTypes" panel={<ObjectTypesTab />} />
        <Tab id="interfaces" title="Interfaces" panel={<InterfacesTab />} />
        <Tab id="relation-types" title="RelationTypes" panel={<RelationTypesTab />} />
        <Tab id="value-types" title="Value Types" panel={<ValueTypesTab />} />
        <Tab id="shared-property-types" title="Shared Property Types" panel={<SharedPropertyTypesTab />} />
        <Tab id="action-types" title="Action Types" panel={<ActionTypesTab />} />
      </Tabs>
    </div>
  );
}
