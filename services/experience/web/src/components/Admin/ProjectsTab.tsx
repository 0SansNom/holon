import { useState } from "react";
import {
  Button,
  Callout,
  Card,
  Dialog,
  DialogBody,
  DialogFooter,
  HTMLSelect,
  InputGroup,
  Spinner,
} from "@blueprintjs/core";
import {
  useProjects,
  usePrincipals,
  useCreateProject,
  useGrantProjectAccess,
  useRevokeProjectAccess,
} from "../../api/hooks";
import type { Project, AccessRelation } from "../../api/identity";
import { ApiError } from "../../api/client";

const RELATIONS: AccessRelation[] = ["viewer", "editor", "admin"];

function ManageProjectAccessDialog({ project, onClose }: { project: Project; onClose: () => void }) {
  const { data: principals = [] } = usePrincipals();
  const [principalUrn, setPrincipalUrn] = useState("");
  const [relation, setRelation] = useState<AccessRelation>("viewer");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const grant = useGrantProjectAccess();
  const revoke = useRevokeProjectAccess();

  async function run(action: "grant" | "revoke") {
    setError(null);
    setOk(null);
    if (!principalUrn) {
      setError("Select a principal first.");
      return;
    }
    try {
      const mutation = action === "grant" ? grant : revoke;
      await mutation.mutateAsync({ projectName: project.name, principalUrn, relation });
      setOk(`${action === "grant" ? "Granted" : "Revoked"} project ${relation}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    }
  }

  return (
    <Dialog isOpen title={`Project access — ${project.name}`} onClose={onClose}>
      <DialogBody>
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
          Grants here are additive on top of workspace-level access, never a replacement for it — an ObjectType
          scoped to this project stays fully readable to every existing workspace <code>viewer</code>/
          <code>editor</code>/<code>admin</code> too.
        </p>
        <HTMLSelect fill value={principalUrn} onChange={(e) => setPrincipalUrn(e.target.value)} style={{ marginBottom: 8 }}>
          <option value="">Select a principal…</option>
          {principals.map((p) => (
            <option key={p.urn} value={p.urn}>
              {p.display_name} ({p.urn})
            </option>
          ))}
        </HTMLSelect>
        <HTMLSelect fill value={relation} onChange={(e) => setRelation(e.target.value as AccessRelation)} options={RELATIONS} />
        {error && (
          <Callout intent="danger" style={{ marginTop: 12 }}>
            {error}
          </Callout>
        )}
        {ok && (
          <Callout intent="success" style={{ marginTop: 12 }}>
            {ok}
          </Callout>
        )}
      </DialogBody>
      <DialogFooter
        actions={
          <>
            <Button intent="danger" loading={revoke.isPending} onClick={() => void run("revoke")}>
              Revoke
            </Button>
            <Button intent="primary" loading={grant.isPending} onClick={() => void run("grant")}>
              Grant
            </Button>
          </>
        }
      />
    </Dialog>
  );
}

export function ProjectsTab() {
  const { data, isLoading } = useProjects();
  const createProject = useCreateProject();
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);
  const [managing, setManaging] = useState<Project | null>(null);

  async function create() {
    setCreateError(null);
    try {
      await createProject.mutateAsync(newName);
      setCreating(false);
      setNewName("");
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "Create failed");
    }
  }

  if (isLoading) return <Spinner />;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)", margin: 0, maxWidth: 560 }}>
          The Org/Space/Project tier under Workspace — an ObjectType can optionally scope down to one, narrowing
          who can read/write it beyond the workspace default, additively.
        </p>
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New project
        </Button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
        {data?.map((project) => (
          <Card key={project.urn}>
            <strong>{project.name}</strong>
            <div className="hl-mono" style={{ fontSize: 11, color: "var(--hl-text-muted)", marginTop: 6 }}>
              {project.urn}
            </div>
            <Button small minimal icon="key" style={{ marginTop: 10 }} onClick={() => setManaging(project)}>
              Manage access
            </Button>
          </Card>
        ))}
        {data?.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No projects yet.</p>}
      </div>

      <Dialog isOpen={creating} onClose={() => setCreating(false)} title="New project">
        <DialogBody>
          <InputGroup placeholder="project-name" value={newName} onChange={(e) => setNewName(e.target.value)} />
          {createError && (
            <Callout intent="danger" style={{ marginTop: 12 }}>
              {createError}
            </Callout>
          )}
        </DialogBody>
        <DialogFooter
          actions={
            <Button intent="primary" disabled={!newName} loading={createProject.isPending} onClick={() => void create()}>
              Create
            </Button>
          }
        />
      </Dialog>

      {managing && <ManageProjectAccessDialog project={managing} onClose={() => setManaging(null)} />}
    </div>
  );
}
