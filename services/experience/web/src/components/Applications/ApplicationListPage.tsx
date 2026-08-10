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
      description={
        <>
          Ontology-linked applications — every binding and action reference is validated against Knowledge's real
          ontology, never free-form.
        </>
      }
      actions={
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New application
        </Button>
      }
    >
      <CardGrid minWidth={260}>
        {data.map((app) => (
          <Link key={app.name} to="/applications/$name" params={{ name: app.name }} className="hl-link-reset">
            <Card interactive className="hl-h-full">
              <div className="hl-registry-card-header">
                <strong>{app.name}</strong>
                <div className="hl-flex-row hl-items-center hl-gap-xs">
                  <Tag intent={app.status === "promoted" ? "success" : "warning"} minimal>
                    {app.status}
                  </Tag>
                  <div onClick={(e) => { e.preventDefault(); e.stopPropagation(); }}>
                    <ResourceActionsMenu urn={app.urn} />
                  </div>
                </div>
              </div>
              <div className="hl-mono hl-text-muted-sm hl-mt-sm">
                v{app.version} · {app.dependencies.objectTypes.join(", ")}
              </div>
              <div className="hl-tag-row hl-mt-sm">
                <ResourceTagBadges urn={app.urn} />
              </div>
            </Card>
          </Link>
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
