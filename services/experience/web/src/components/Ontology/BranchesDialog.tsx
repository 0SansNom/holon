import { Suspense, useState } from "react";
import { Button, Dialog, DialogBody, FormGroup, InputGroup, Tag, Callout } from "@blueprintjs/core";
import Editor, { DiffEditor } from "@monaco-editor/react";
import {
  useBranches,
  useBranchReviews,
  useCreateBranch,
  useReviewBranch,
  useUpdateBranchDraft,
  useObjectTypeVersions,
  type BranchKind,
} from "../../api/hooks";
import type { Branch } from "../../api/knowledge";
import { ApiError } from "../../api/client";
import { ErrorCallout, EmptyState } from "../common/ListPrimitives";
import { FormattedValue } from "../common/PropertyFormat";
import { BranchesDialogSkeleton } from "../common/Skeleton";
import { useMonacoEditorTheme } from "../../hooks/useMonacoEditorTheme";

const OBJECT_TYPE_BRANCH_FIELDS = [
  "property_mapping",
  "description",
  "implements",
  "derived_properties",
  "project_urn",
  "markings",
  "property_formats",
  "conditional_formats",
  "property_types",
  "link_constraint_bindings",
] as const;

function proposedDefinitionForObjectType(branch: Branch, versions: ReturnType<typeof useObjectTypeVersions>["data"]) {
  const version = versions?.find((v) => v.version === branch.version);
  if (!version) return null;
  const out: Record<string, unknown> = {};
  for (const field of OBJECT_TYPE_BRANCH_FIELDS) out[field] = version[field as keyof typeof version];
  return out;
}

function proposedDefinitionGeneric(branch: Branch): Record<string, unknown> | null {
  if (!branch.proposed_definition) return null;
  try {
    return JSON.parse(branch.proposed_definition) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function BranchDetail({
  kind,
  resourceName,
  branch,
  currentDefinition,
}: {
  kind: BranchKind;
  resourceName: string;
  branch: Branch;
  currentDefinition: Record<string, unknown>;
}) {
  const monacoTheme = useMonacoEditorTheme();
  const { data: versions } = useObjectTypeVersions(kind === "object_type" ? resourceName : "");
  const { data: reviews = [] } = useBranchReviews(kind, resourceName, branch.branch_name);
  const updateDraft = useUpdateBranchDraft(kind, resourceName, branch.branch_name);
  const review = useReviewBranch(kind, resourceName, branch.branch_name);

  const proposed = kind === "object_type" ? proposedDefinitionForObjectType(branch, versions) : proposedDefinitionGeneric(branch);

  const [draftJson, setDraftJson] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);

  if (kind === "object_type" && !versions) return null;
  if (proposed === null) return <Callout intent="warning">Couldn't resolve this branch's proposed definition.</Callout>;

  const effectiveDraftJson = draftJson ?? JSON.stringify(proposed, null, 2);

  async function saveDraft() {
    setError(null);
    let parsed;
    try {
      parsed = JSON.parse(effectiveDraftJson);
    } catch {
      setError("Proposed definition must be valid JSON.");
      return;
    }
    try {
      await updateDraft.mutateAsync(parsed);
      setDraftJson(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Update failed");
    }
  }

  async function decide(decision: "approved" | "changes_requested") {
    setError(null);
    try {
      await review.mutateAsync({ decision, note: note || undefined });
      setNote("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Review failed");
    }
  }

  return (
    <div>
      <div className="hl-branch-detail-header">
        <div>
          <strong>{branch.branch_name}</strong>{" "}
          <Tag minimal intent={branch.status === "merged" ? "success" : "none"}>
            {branch.status}
          </Tag>
        </div>
        <span className="hl-text-muted-sm">
          {branch.created_by_urn.split(":").pop()} ·{" "}
          <FormattedValue value={branch.created_at} rule={{ kind: "datetime", style: "relative" }} />
        </span>
      </div>

      <div className="hl-section-title hl-mb-xs">Current vs. proposed</div>
      <div className="hl-json-editor hl-mb-md">
        <DiffEditor
          key={branch.id}
          height="220px"
          language="json"
          theme={monacoTheme}
          original={JSON.stringify(currentDefinition, null, 2)}
          modified={JSON.stringify(proposed, null, 2)}
          keepCurrentOriginalModel
          keepCurrentModifiedModel
          options={{ readOnly: true, renderSideBySide: true, minimap: { enabled: false }, fontSize: 12 }}
        />
      </div>

      {reviews.length > 0 && (
        <div className="hl-mb-md">
          <div className="hl-section-title hl-mb-xs">Review history</div>
          <div className="hl-grid-gap-sm">
            {reviews.map((r) => (
              <div key={r.id} className="hl-review-row">
                <Tag minimal intent={r.decision === "approved" ? "success" : "warning"}>
                  {r.decision}
                </Tag>
                <span className="hl-text-muted">{r.reviewer_urn.split(":").pop()}</span>
                {r.note && <span>— {r.note}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {branch.status === "open" && (
        <>
          <FormGroup label="Proposed definition (editable)">
            <div className="hl-json-editor">
              <Editor
                height="160px"
                defaultLanguage="json"
                theme={monacoTheme}
                value={effectiveDraftJson}
                onChange={(v) => setDraftJson(v ?? "")}
                options={{ minimap: { enabled: false }, fontSize: 12 }}
              />
            </div>
          </FormGroup>
          <Button small loading={updateDraft.isPending} onClick={() => void saveDraft()} className="hl-mb-md">
            Update draft
          </Button>

          <FormGroup label="Review note (optional)">
            <InputGroup value={note} onChange={(e) => setNote(e.target.value)} />
          </FormGroup>
          <div className="hl-flex-row hl-gap-sm">
            <Button intent="success" loading={review.isPending} onClick={() => void decide("approved")}>
              Approve
            </Button>
            <Button intent="warning" loading={review.isPending} onClick={() => void decide("changes_requested")}>
              Request changes
            </Button>
          </div>
        </>
      )}

      {error && <ErrorCallout>{error}</ErrorCallout>}
    </div>
  );
}

export function BranchesDialog({
  kind,
  resourceName,
  currentDefinition,
  onClose,
}: {
  kind: BranchKind;
  resourceName: string;
  currentDefinition: Record<string, unknown>;
  onClose: () => void;
}) {
  return (
    <Dialog isOpen title={`${resourceName} — branches`} onClose={onClose} style={{ width: 860 }}>
      <Suspense
        fallback={
          <DialogBody>
            <BranchesDialogSkeleton />
          </DialogBody>
        }
      >
        <BranchesDialogBody kind={kind} resourceName={resourceName} currentDefinition={currentDefinition} />
      </Suspense>
    </Dialog>
  );
}

function BranchesDialogBody({
  kind,
  resourceName,
  currentDefinition,
}: {
  kind: BranchKind;
  resourceName: string;
  currentDefinition: Record<string, unknown>;
}) {
  const monacoTheme = useMonacoEditorTheme();
  const { data: branches = [] } = useBranches(kind, resourceName);
  const createBranch = useCreateBranch(kind, resourceName);
  const [selected, setSelected] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newBranchName, setNewBranchName] = useState("");
  const [newBranchJson, setNewBranchJson] = useState(() => JSON.stringify(currentDefinition, null, 2));
  const [createError, setCreateError] = useState<string | null>(null);

  const selectedBranch = branches.find((b) => b.branch_name === selected) ?? null;

  async function create() {
    setCreateError(null);
    let definition;
    try {
      definition = JSON.parse(newBranchJson);
    } catch {
      setCreateError("Proposed definition must be valid JSON.");
      return;
    }
    try {
      await createBranch.mutateAsync({ branch_name: newBranchName, definition });
      setCreating(false);
      setNewBranchName("");
      setSelected(newBranchName);
    } catch (err) {
      setCreateError(err instanceof ApiError ? err.message : "Create failed");
    }
  }

  return (
    <DialogBody>
      <div className="hl-branches-layout">
        <div className="hl-branches-sidebar">
          <Button small fill icon="add" onClick={() => setCreating((c) => !c)} className="hl-mb-sm">
            New branch
          </Button>
          {creating && (
            <div className="hl-mb-md">
              <FormGroup label="Branch name">
                <InputGroup small value={newBranchName} onChange={(e) => setNewBranchName(e.target.value)} />
              </FormGroup>
              <div className="hl-json-editor hl-mb-sm">
                <Editor
                  height="140px"
                  defaultLanguage="json"
                  theme={monacoTheme}
                  value={newBranchJson}
                  onChange={(v) => setNewBranchJson(v ?? "")}
                  options={{ minimap: { enabled: false }, fontSize: 11 }}
                />
              </div>
              {createError && <ErrorCallout>{createError}</ErrorCallout>}
              <Button small intent="primary" fill disabled={!newBranchName} loading={createBranch.isPending} onClick={() => void create()}>
                Create
              </Button>
            </div>
          )}

          <div className="hl-grid-gap-sm">
            {branches.map((b) => (
              <div
                key={b.id}
                className="hl-branch-item"
                data-active={b.branch_name === selected}
                onClick={() => setSelected(b.branch_name)}
                onKeyDown={(e) => e.key === "Enter" && setSelected(b.branch_name)}
                role="button"
                tabIndex={0}
              >
                <div className="hl-branch-item-name">{b.branch_name}</div>
                <Tag minimal intent={b.status === "merged" ? "success" : "none"} className="hl-mt-xs">
                  {b.status}
                </Tag>
              </div>
            ))}
            {branches.length === 0 && <EmptyState>No branches yet.</EmptyState>}
          </div>
        </div>

        <div className="hl-branches-main">
          {selectedBranch ? (
            <Suspense fallback={<BranchesDialogSkeleton />}>
              <BranchDetail kind={kind} resourceName={resourceName} branch={selectedBranch} currentDefinition={currentDefinition} />
            </Suspense>
          ) : (
            <EmptyState>Select a branch, or create one.</EmptyState>
          )}
        </div>
      </div>
    </DialogBody>
  );
}
