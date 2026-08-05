import { useState } from "react";
import { Link, useNavigate } from "@tanstack/react-router";
import { Button, Card, Dialog, DialogBody, DialogFooter, H3, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { useApplications } from "../../api/hooks";

export function ApplicationListPage() {
  const { data, isLoading } = useApplications();
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const navigate = useNavigate();

  if (isLoading) return <Spinner />;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <H3>Applications</H3>
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New application
        </Button>
      </div>
      <p style={{ color: "var(--hl-text-muted)", marginBottom: 20 }}>
        Ontology-linked applications — every binding and action reference is validated against Knowledge's
        real ontology, never free-form.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
        {data?.map((app) => (
          <Link key={app.name} to="/applications/$name" params={{ name: app.name }} style={{ textDecoration: "none" }}>
            <Card interactive style={{ height: "100%" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start" }}>
                <strong style={{ color: "var(--hl-text)" }}>{app.name}</strong>
                <Tag intent={app.status === "promoted" ? "success" : "warning"} minimal>
                  {app.status}
                </Tag>
              </div>
              <div className="hl-mono" style={{ fontSize: 11, color: "var(--hl-text-muted)", marginTop: 10 }}>
                v{app.version} · {app.dependencies.objectTypes.join(", ")}
              </div>
            </Card>
          </Link>
        ))}
        {data?.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No applications yet.</p>}
      </div>

      <Dialog isOpen={creating} onClose={() => setCreating(false)} title="New application">
        <DialogBody>
          <InputGroup placeholder="application-name" value={newName} onChange={(e) => setNewName(e.target.value)} />
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
              Create
            </Button>
          }
        />
      </Dialog>
    </div>
  );
}
