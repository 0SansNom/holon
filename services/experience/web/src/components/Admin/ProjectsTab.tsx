import { useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Button,
  Callout,
  Card,
  Dialog,
  DialogBody,
  DialogFooter,
  HTMLSelect,
  InputGroup,
} from "@blueprintjs/core";
import {
  useProjects,
  usePrincipals,
  useCreateProject,
  useGrantProjectAccess,
  useRevokeProjectAccess,
} from "../../api/hooks";
import type { Project, AccessRelation } from "../../api/identity";
import { getErrorMessage } from "../../api/client";
import { CardGrid, EmptyState, ErrorCallout } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { OntologyTabHeader } from "../Ontology/OntologyTabLayout";

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
      setError(getErrorMessage(err));
    }
  }

  return (
    <Dialog isOpen title={`Project access — ${project.name}`} onClose={onClose}>
      <DialogBody>
        <p className="hl-text-muted">
          Grants here are additive on top of workspace-level access, never a replacement for it — an ObjectType
          scoped to this project stays fully readable to every existing workspace <code>viewer</code>/
          <code>editor</code>/<code>admin</code> too.
        </p>
        <HTMLSelect fill value={principalUrn} onChange={(e) => setPrincipalUrn(e.target.value)} className="hl-mb-sm">
          <option value="">Select a principal…</option>
          {principals.map((p) => (
            <option key={p.urn} value={p.urn}>
              {p.display_name} ({p.urn})
            </option>
          ))}
        </HTMLSelect>
        <HTMLSelect fill value={relation} onChange={(e) => setRelation(e.target.value as AccessRelation)} options={RELATIONS} />
        {error && <ErrorCallout>{error}</ErrorCallout>}
        {ok && (
          <Callout intent="success" className="hl-mt-sm">
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
  const { data } = useProjects();
  const createProject = useCreateProject();
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [managing, setManaging] = useState<Project | null>(null);

  usePaletteCreateIntent("create-project", setCreating);

  function closeCreate() {
    setCreating(false);
    setNewName("");
  }

  const {
    submit: submitCreate,
    error: createError,
    isPending: createPending,
  } = useAsyncAction(async () => {
    await createProject.mutateAsync(newName);
    closeCreate();
  }, { successMessage: `Project "${newName}" created` });

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            The Org/Space/Project tier under Workspace — an ObjectType can optionally scope down to one, narrowing
            who can read/write it beyond the workspace default, additively.
          </>
        }
        createLabel="New project"
        onCreate={() => setCreating(true)}
      />

      <CardGrid>
        {data?.map((project) => (
          <Card key={project.urn}>
            <strong className="hl-registry-card-title">{project.name}</strong>
            <div className="hl-mono hl-text-muted-sm hl-mt-xs">{project.urn}</div>
            <div className="hl-card-actions">
              <Link to="/admin/projects/$name" params={{ name: project.name }}>
                <Button small minimal>
                  Open
                </Button>
              </Link>
              <Button small minimal icon="key" onClick={() => setManaging(project)}>
                Manage access
              </Button>
            </div>
          </Card>
        ))}
        {data?.length === 0 && (
          <EmptyState actionLabel="New project" onAction={() => setCreating(true)}>
            No projects yet.
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New project"
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!newName}
        onSubmit={() => void submitCreate(undefined)}
      >
        <InputGroup placeholder="project-name" value={newName} onChange={(e) => setNewName(e.target.value)} />
      </RegistryDialog>

      {managing && <ManageProjectAccessDialog project={managing} onClose={() => setManaging(null)} />}
    </div>
  );
}
