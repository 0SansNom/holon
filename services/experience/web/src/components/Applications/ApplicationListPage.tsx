import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { Button, Card, Dialog, DialogBody, DialogFooter, InputGroup, Tag } from "@blueprintjs/core";
import { useApplications } from "../../api/hooks";
import { ResourceActionsMenu, ResourceTagBadges } from "../common/ResourceActionsMenu";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";

export function ApplicationListPage() {
  const { data } = useApplications();
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const navigate = useNavigate();

  return (
    <RegistryPage
      title="Applications"
      description="Apps built on your ontology — dashboards, object views, and actions, always checked against real types."
      actions={
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New application
        </Button>
      }
    >
      <CardGrid minWidth={260}>
        {data.map((app) => (
          <Card key={app.name} className="hl-h-full">
              <div className="hl-registry-card-header">
                <Link to="/applications/$name" params={{ name: app.name }} className="hl-link-reset">
                  <strong>{app.name}</strong>
                </Link>
                <div className="hl-flex-row hl-items-center hl-gap-xs">
                  <Tag intent={app.status === "promoted" ? "success" : "warning"} minimal>
                    {app.status}
                  </Tag>
                  <ResourceActionsMenu urn={app.urn} />
                </div>
              </div>
              <Link to="/applications/$name" params={{ name: app.name }} className="hl-link-reset">
                <div className="hl-mono hl-text-muted-sm hl-mt-sm">
                  v{app.version} · {app.dependencies.objectTypes.join(", ")}
                </div>
                <div className="hl-tag-row hl-mt-sm">
                  <ResourceTagBadges urn={app.urn} />
                </div>
              </Link>
          </Card>
        ))}
        {data.length === 0 && (
          <EmptyState actionLabel="New application" onAction={() => setCreating(true)}>
            No applications yet.
          </EmptyState>
        )}
      </CardGrid>

      <Dialog isOpen={creating} onClose={() => setCreating(false)} title="New application">
        <DialogBody>
          <InputGroup
            placeholder="my-app"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newName) {
                setCreating(false);
                void navigate({ to: "/applications/$name", params: { name: newName } });
              }
            }}
          />
        </DialogBody>
        <DialogFooter
          actions={
            <Button
              intent="primary"
              disabled={!newName}
              onClick={() => {
                setCreating(false);
                void navigate({ to: "/applications/$name", params: { name: newName } });
              }}
            >
              Open builder
            </Button>
          }
        />
      </Dialog>
    </RegistryPage>
  );
}
