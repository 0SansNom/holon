import { useState } from "react";
import { Link, useNavigate, useParams } from "@tanstack/react-router";
import { Alert, Button, Callout, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { useDeletePipeline, usePipeline, usePipelineRuns, useRunPipeline, useSetPipelineSchedule } from "../../api/hooks";
import type { PipelineRun } from "../../api/connectivity";
import { DetailPage, PageSection } from "../common/PageLayout";
import { EmptyState } from "../common/ListPrimitives";
import { getErrorMessage } from "../../api/client";
import { showSuccess } from "../../lib/toast";
import { PipelineEditorDialog } from "./PipelineEditorDialog";

function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function statusIntent(status: string): "success" | "danger" | "primary" | "none" {
  if (status === "succeeded") return "success";
  if (status === "failed") return "danger";
  return "none";
}

function formatLag(seconds: number): string {
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

function RunCard({ run }: { run: PipelineRun }) {
  const navigate = useNavigate();
  return (
    <div className="hl-panel hl-mb-sm">
      <div className="hl-flex-between hl-mb-xs">
        <div className="hl-flex-row hl-items-center hl-gap-sm">
          <Tag minimal intent={statusIntent(run.status)}>
            {run.status}
          </Tag>
          <span className="hl-mono hl-text-muted-sm">#{run.id}</span>
        </div>
        <span className="hl-text-muted-sm">
          {formatWhen(run.started_at)}
          {run.finished_at ? ` → ${formatWhen(run.finished_at)}` : ""}
        </span>
      </div>
      {run.error && (
        <Callout intent="danger" className="hl-mb-sm">
          {run.error}
        </Callout>
      )}
      {(run.step_results ?? []).length > 0 && (
        <table className="hl-data-table hl-data-table-compact">
          <thead>
            <tr>
              <th>Step</th>
              <th>Rows</th>
              <th>Output</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {(run.step_results ?? []).map((step) => (
              <tr key={`${run.id}-${step.step_name}`} className="hl-data-table-row">
                <td className="hl-mono">{step.step_name}</td>
                <td>{step.row_count.toLocaleString()}</td>
                <td>
                  <Link
                    to="/catalog"
                    search={{ dataset: step.dataset_urn.split(":").at(-1) }}
                    className="hl-link-accent"
                  >
                    {step.dataset_urn.split(":").at(-1)}
                  </Link>
                </td>
                <td>
                  <Button
                    small
                    minimal
                    icon="flow-linear"
                    title="View lineage"
                    onClick={() =>
                      void navigate({
                        to: "/lineage/$urn",
                        params: { urn: step.dataset_version_urn },
                      })
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export function PipelineDetailPage() {
  const { name } = useParams({ from: "/shell/pipelines/$name" });
  const navigate = useNavigate();
  const { data: pipeline, isPending } = usePipeline(name);
  const { data: runs, isLoading: runsLoading, refetch } = usePipelineRuns(name);
  const runMutation = useRunPipeline(name);
  const deletePipeline = useDeletePipeline();
  const setSchedule = useSetPipelineSchedule();
  const [runError, setRunError] = useState<string | null>(null);
  const [lastSucceededId, setLastSucceededId] = useState<number | null>(null);
  const [editing, setEditing] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [scheduleDraft, setScheduleDraft] = useState<string>(
    pipeline?.schedule_interval_minutes != null ? String(pipeline.schedule_interval_minutes) : "",
  );

  if (isPending) {
    return (
      <DetailPage breadcrumbs={[{ label: "Pipelines", to: "/pipelines" }, { label: name }]} title={name}>
        <Spinner />
      </DetailPage>
    );
  }

  if (!pipeline) {
    return (
      <DetailPage breadcrumbs={[{ label: "Pipelines", to: "/pipelines" }, { label: name }]} title={name}>
        <EmptyState>Pipeline not found.</EmptyState>
      </DetailPage>
    );
  }

  const scheduleMinutes = pipeline.schedule_interval_minutes;

  async function commitSchedule() {
    const minutes = scheduleDraft.trim() === "" ? null : Number(scheduleDraft);
    if (minutes === scheduleMinutes) return;
    try {
      await setSchedule.mutateAsync({ name, scheduleIntervalMinutes: minutes });
    } catch (err) {
      setRunError(getErrorMessage(err));
    }
  }

  async function handleRun() {
    setRunError(null);
    setLastSucceededId(null);
    try {
      const result = await runMutation.mutateAsync();
      setLastSucceededId(result.id);
      void refetch();
    } catch (err) {
      setRunError(getErrorMessage(err));
      void refetch();
    }
  }

  async function handleDelete() {
    setConfirmingDelete(false);
    try {
      await deletePipeline.mutateAsync(name);
      showSuccess(`Pipeline "${name}" deleted`);
      void navigate({ to: "/pipelines" });
    } catch (err) {
      setRunError(getErrorMessage(err));
    }
  }

  return (
    <DetailPage
      breadcrumbs={[{ label: "Pipelines", to: "/pipelines" }, { label: pipeline.name }]}
      title={pipeline.name}
      description={
        <>
          {pipeline.steps.length} step{pipeline.steps.length === 1 ? "" : "s"} · updated{" "}
          {formatWhen(pipeline.updated_at)}
        </>
      }
      actions={
        <div className="hl-flex-row hl-gap-sm">
          <Button icon="edit" onClick={() => setEditing(true)}>
            Edit
          </Button>
          <Button icon="play" intent="primary" loading={runMutation.isPending} onClick={() => void handleRun()}>
            Run pipeline
          </Button>
          <Button
            icon="trash"
            intent="danger"
            minimal
            loading={deletePipeline.isPending}
            onClick={() => setConfirmingDelete(true)}
          >
            Delete
          </Button>
        </div>
      }
    >
      {runError && (
        <Callout intent="danger" className="hl-mb-md">
          {runError}
        </Callout>
      )}
      {lastSucceededId !== null && (
        <Callout intent="success" className="hl-mb-md">
          Run #{lastSucceededId} succeeded — outputs catalogued with derived_from lineage.
        </Callout>
      )}

      <PageSection title="Health & schedule">
        <div className="hl-flex-row hl-items-center hl-gap-md hl-mb-sm">
          {pipeline.last_run ? (
            <span className="hl-flex-row hl-items-center hl-gap-sm">
              <Tag minimal intent={statusIntent(pipeline.last_run.status)}>
                {pipeline.last_run.status}
              </Tag>
              <span className="hl-text-muted-sm">{pipeline.last_run.row_count.toLocaleString()} rows</span>
            </span>
          ) : (
            <span className="hl-text-muted-sm">never run</span>
          )}
          <span className="hl-text-muted-sm">
            {pipeline.lag_seconds != null
              ? `last success ${formatLag(pipeline.lag_seconds)}`
              : "no successful run yet"}
          </span>
        </div>
        <div className="hl-flex-row hl-items-center hl-gap-sm">
          <span className="hl-text-muted-sm">Run every</span>
          <InputGroup
            type="number"
            min={1}
            small
            placeholder="manual"
            value={scheduleDraft}
            onChange={(e) => setScheduleDraft(e.target.value)}
            onBlur={() => void commitSchedule()}
            disabled={setSchedule.isPending}
            style={{ width: 84 }}
          />
          <span className="hl-text-muted-sm">minutes — blank for manual only</span>
        </div>
      </PageSection>

      <PageSection title="Steps">
        <div className="hl-pipeline-steps">
          {pipeline.steps.map((step, index) => (
            <div key={step.step_name} className="hl-pipeline-step">
              <div className="hl-pipeline-step-index">{index + 1}</div>
              <div className="hl-flex-1 hl-min-w-0">
                <div className="hl-flex-row hl-items-center hl-gap-sm hl-mb-xs">
                  <strong>{step.step_name}</strong>
                  <Tag minimal className="hl-mono">
                    {step.function_name}
                  </Tag>
                </div>
                <div className="hl-text-muted-sm">
                  <Link to="/catalog" search={{ dataset: step.input_dataset }} className="hl-link-accent">
                    {step.input_dataset}
                  </Link>
                  <span className="hl-mx-xs">→</span>
                  <span className="hl-mono">{step.function_name}</span>
                  <span className="hl-mx-xs">→</span>
                  <Link to="/catalog" search={{ dataset: step.output_dataset }} className="hl-link-accent">
                    {step.output_dataset}
                  </Link>
                </div>
              </div>
            </div>
          ))}
        </div>
      </PageSection>

      <PageSection title="Recent runs">
        {runsLoading && (
          <div className="hl-flex-row hl-items-center hl-gap-sm">
            <Spinner size={16} />
            <span className="hl-text-muted-sm">Loading runs…</span>
          </div>
        )}
        {!runsLoading && (runs ?? []).length === 0 && (
          <p className="hl-text-muted">No runs yet — click Run pipeline.</p>
        )}
        {(runs ?? []).slice(0, 20).map((run) => (
          <RunCard key={run.id} run={run} />
        ))}
      </PageSection>

      <PipelineEditorDialog
        isOpen={editing}
        onClose={() => setEditing(false)}
        pipeline={pipeline}
      />

      <Alert
        isOpen={confirmingDelete}
        intent="danger"
        icon="trash"
        confirmButtonText="Delete"
        cancelButtonText="Cancel"
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmingDelete(false)}
      >
        <p>
          Delete pipeline <strong>{pipeline.name}</strong>? The definition and run history are removed.
          Catalogued datasets produced by past runs are kept.
        </p>
      </Alert>
    </DetailPage>
  );
}
