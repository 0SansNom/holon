import { useEffect, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Alert, Button, Tag } from "@blueprintjs/core";
import { useDeletePipeline, usePipelines } from "../../api/hooks";
import { EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { showSuccess } from "../../lib/toast";
import { usePaletteIntentStore } from "../../store/paletteIntent";
import { PipelineEditorDialog } from "./PipelineEditorDialog";

function formatWhen(value: string): string {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function statusIntent(status: string): "success" | "danger" | "none" {
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

export function PipelinesPage() {
  const { data: pipelines } = usePipelines();
  const deletePipeline = useDeletePipeline();
  const [creating, setCreating] = useState(false);
  const [deletingName, setDeletingName] = useState<string | null>(null);
  const intent = usePaletteIntentStore((s) => s.intent);
  const consumeIntent = usePaletteIntentStore((s) => s.consume);

  useEffect(() => {
    if (intent === "create-pipeline") {
      setCreating(true);
      consumeIntent();
    }
  }, [intent, consumeIntent]);

  return (
    <RegistryPage
      title="Pipelines"
      description="Transform datasets step by step. Each run writes a new snapshot, catalogued with lineage automatically."
      actions={
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New pipeline
        </Button>
      }
    >
      {(pipelines ?? []).length === 0 && (
        <EmptyState actionLabel="New pipeline" onAction={() => setCreating(true)}>
          No pipelines yet — create one to transform catalogued datasets.
        </EmptyState>
      )}

      {(pipelines ?? []).length > 0 && (
        <div className="hl-panel hl-table-scroll">
          <table className="hl-data-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Steps</th>
                <th>Health</th>
                <th>Schedule</th>
                <th>Updated</th>
                <th aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {(pipelines ?? []).map((p) => (
                <tr key={p.name} className="hl-data-table-row">
                  <td>
                    <Link to="/pipelines/$name" params={{ name: p.name }} className="hl-link-accent">
                      {p.name}
                    </Link>
                  </td>
                  <td>
                    <Tag minimal>
                      {p.steps.length} step{p.steps.length === 1 ? "" : "s"}
                    </Tag>
                  </td>
                  <td>
                    {p.last_run ? (
                      <span className="hl-flex-row hl-items-center hl-gap-sm">
                        <Tag minimal intent={statusIntent(p.last_run.status)}>
                          {p.last_run.status}
                        </Tag>
                        {p.lag_seconds != null && (
                          <span className="hl-text-muted-sm">{formatLag(p.lag_seconds)}</span>
                        )}
                      </span>
                    ) : (
                      <span className="hl-text-muted-sm">never run</span>
                    )}
                  </td>
                  <td className="hl-text-muted-sm">
                    {p.schedule_interval_minutes != null ? `every ${p.schedule_interval_minutes}min` : "manual"}
                  </td>
                  <td className="hl-text-muted-sm">{formatWhen(p.updated_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    <Button
                      small
                      minimal
                      icon="trash"
                      intent="danger"
                      disabled={deletePipeline.isPending}
                      onClick={() => setDeletingName(p.name)}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <PipelineEditorDialog isOpen={creating} onClose={() => setCreating(false)} />

      <Alert
        isOpen={!!deletingName}
        intent="danger"
        icon="trash"
        confirmButtonText="Delete"
        cancelButtonText="Cancel"
        onConfirm={() => {
          const name = deletingName;
          setDeletingName(null);
          if (!name) return;
          void deletePipeline.mutateAsync(name).then(() => {
            showSuccess(`Pipeline "${name}" deleted`);
          });
        }}
        onCancel={() => setDeletingName(null)}
      >
        <p>
          Delete pipeline <strong>{deletingName}</strong>? The definition and run history are removed.
          Catalogued datasets produced by past runs are kept.
        </p>
      </Alert>
    </RegistryPage>
  );
}
