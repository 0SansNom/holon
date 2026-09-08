import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Button, Callout, FormGroup, HTMLSelect, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import {
  useApprovals,
  useApproveApproval,
  useRejectApproval,
} from "../../api/hooks";
import type { ActionApproval, ActionApprovalStatus } from "../../api/knowledge";
import { EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { RegistryDialog } from "../common/RegistryDialog";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { parseInstanceUrn, urnShortName } from "../ObjectExplorer/objectExplorerUtils";

const STATUS_FILTERS: Array<{ value: ActionApprovalStatus | "all"; label: string }> = [
  { value: "pending", label: "Pending" },
  { value: "approved", label: "Approved" },
  { value: "rejected", label: "Rejected" },
  { value: "expired", label: "Expired" },
  { value: "failed", label: "Failed" },
  { value: "all", label: "All" },
];

function formatWhen(value: string): string {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function statusIntent(status: ActionApprovalStatus): "primary" | "success" | "danger" | "warning" | "none" {
  if (status === "pending") return "primary";
  if (status === "approved") return "success";
  if (status === "rejected" || status === "failed") return "danger";
  if (status === "expired") return "warning";
  return "none";
}

export function ApprovalsPage() {
  const [statusFilter, setStatusFilter] = useState<ActionApprovalStatus | "all">("pending");
  const { data: approvals, isLoading, error } = useApprovals(
    statusFilter === "all" ? undefined : statusFilter,
  );
  const approveMutation = useApproveApproval();
  const rejectMutation = useRejectApproval();

  const [decision, setDecision] = useState<{ approval: ActionApproval; kind: "approve" | "reject" } | null>(null);
  const [note, setNote] = useState("");

  const sorted = useMemo(
    () =>
      [...(approvals ?? [])].sort(
        (a, b) => new Date(b.requested_at).getTime() - new Date(a.requested_at).getTime(),
      ),
    [approvals],
  );

  const {
    submit: submitDecision,
    error: decisionError,
    isPending: decisionPending,
  } = useAsyncAction(async () => {
    if (!decision) return;
    if (decision.kind === "approve") {
      await approveMutation.mutateAsync({ id: decision.approval.id, note: note || undefined });
    } else {
      await rejectMutation.mutateAsync({ id: decision.approval.id, note: note || undefined });
    }
    setDecision(null);
    setNote("");
  }, {
    successMessage:
      decision?.kind === "approve"
        ? `Approved #${decision.approval.id}`
        : `Rejected #${decision?.approval.id ?? ""}`,
  });

  const canDecideError =
    decisionError && decisionError.includes("rebac_denied")
      ? "You need the approve permission (workspace admin) to decide this request."
      : decisionError;

  return (
    <RegistryPage
      title="Approvals"
      description="High-risk actions wait here until someone with approval rights decides. Editors request; admins approve or reject."
      trailing={
        <HTMLSelect
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as ActionApprovalStatus | "all")}
        >
          {STATUS_FILTERS.map((f) => (
            <option key={f.value} value={f.value}>
              {f.label}
            </option>
          ))}
        </HTMLSelect>
      }
    >
      {isLoading && (
        <div className="hl-flex-row hl-items-center hl-gap-sm">
          <Spinner size={20} />
          <span className="hl-text-muted">Loading approvals…</span>
        </div>
      )}

      {error && (
        <Callout intent="danger" className="hl-mb-md">
          {(error as Error).message}
        </Callout>
      )}

      {!isLoading && !error && sorted.length === 0 && (
        <EmptyState>
          {statusFilter === "pending"
            ? "No pending approvals."
            : `No ${statusFilter === "all" ? "" : `${statusFilter} `}approvals.`}
        </EmptyState>
      )}

      {!isLoading && sorted.length > 0 && (
        <div className="hl-panel hl-table-scroll">
          <table className="hl-data-table">
            <thead>
              <tr>
                <th>Action</th>
                <th>Instance</th>
                <th>Requested by</th>
                <th>Reason</th>
                <th>Requested</th>
                <th>Expires</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {sorted.map((row) => {
                const ref = parseInstanceUrn(row.instance_urn);
                return (
                  <tr key={row.id} className="hl-data-table-row">
                    <td className="hl-mono">{row.action_name}</td>
                    <td>
                      {ref ? (
                        <Link to="/objects/$type/$id" params={{ type: ref.type, id: ref.id }} className="hl-link-accent">
                          {ref.type}/{ref.id}
                        </Link>
                      ) : (
                        <span className="hl-mono hl-text-muted-sm">{row.instance_urn}</span>
                      )}
                    </td>
                    <td className="hl-mono hl-text-muted-sm">{urnShortName(row.requested_by_urn)}</td>
                    <td>{row.reason || "—"}</td>
                    <td className="hl-text-muted-sm">{formatWhen(row.requested_at)}</td>
                    <td className="hl-text-muted-sm">{formatWhen(row.expires_at)}</td>
                    <td>
                      <Tag minimal intent={statusIntent(row.status)}>
                        {row.status}
                      </Tag>
                    </td>
                    <td>
                      {row.status === "pending" && (
                        <div className="hl-flex-row hl-gap-xs">
                          <Button
                            intent="success"
                            icon="tick"
                            onClick={() => {
                              setNote("");
                              setDecision({ approval: row, kind: "approve" });
                            }}
                          >
                            Approve
                          </Button>
                          <Button
                            intent="danger"
                            icon="cross"
                            onClick={() => {
                              setNote("");
                              setDecision({ approval: row, kind: "reject" });
                            }}
                          >
                            Reject
                          </Button>
                        </div>
                      )}
                      {row.status !== "pending" && row.decision_note && (
                        <span className="hl-text-muted-sm" title={row.decision_note}>
                          {row.decision_note}
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <RegistryDialog
        isOpen={!!decision}
        title={decision?.kind === "approve" ? "Approve Action" : "Reject Action"}
        onClose={() => setDecision(null)}
        error={canDecideError}
        isPending={decisionPending}
        submitLabel={decision?.kind === "approve" ? "Approve" : "Reject"}
        onSubmit={() => submitDecision(undefined)}
      >
        {decision && (
          <>
            <p className="hl-body-text hl-mb-md">
              <span className="hl-mono">{decision.approval.action_name}</span> on{" "}
              <span className="hl-mono">{decision.approval.instance_urn.split(":").at(-1)}</span>
              {decision.approval.reason ? (
                <>
                  {" "}
                  — reason: <em>{decision.approval.reason}</em>
                </>
              ) : null}
            </p>
            {decision.kind === "approve" && (
              <p className="hl-text-muted-sm hl-mb-md">
                Approving applies the mutation (Step 1). Automation may then run external writeback (Step 2).
              </p>
            )}
            <FormGroup label="Decision note (optional)">
              <InputGroup
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder={decision.kind === "approve" ? "Confirmed with fraud team" : "Insufficient evidence"}
                autoFocus
              />
            </FormGroup>
          </>
        )}
      </RegistryDialog>
    </RegistryPage>
  );
}
