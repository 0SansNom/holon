import { H3, Tab, Tabs } from "@blueprintjs/core";
import { PrincipalsTab } from "./PrincipalsTab";
import { ProjectsTab } from "./ProjectsTab";

// self-service admin UI for principal/workspace/project
// management — every endpoint this renders (`GET /principals`,
// `POST /principals/{urn}/access/*`, the Project CRUD/access endpoints
// from Phase B.1) has been API-only since it was built; this is the
// frontend catching up, not a new backend capability.
export function AdminPage() {
  return (
    <div>
      <H3>Admin</H3>
      <p style={{ color: "var(--hl-text-muted)", marginBottom: 20 }}>
        Principal, workspace, and project access management — the same governance actions the CLI's{" "}
        <code>holon principals</code>/<code>holon workspace</code>/<code>holon projects</code> commands wrap,
        here as a self-service UI.
      </p>
      <Tabs id="admin-tabs" renderActiveTabPanelOnly>
        <Tab id="principals" title="Principals" panel={<PrincipalsTab />} />
        <Tab id="projects" title="Projects" panel={<ProjectsTab />} />
      </Tabs>
    </div>
  );
}
