import { useState } from "react";
import { Alert, Button, Card, Switch, Tag } from "@blueprintjs/core";
import { useSyncDataset, useDisableSqlSource, useEnableSqlSource, useDeleteSqlSource } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { SqlSource } from "../../api/connectivity";
import { nextSyncDescription } from "./shared";
import { CreateObjectTypeDialog } from "./CreateObjectTypeDialog";

export function SqlSourceRow({
  source,
  lastSync,
  onEdit,
}: {
  source: SqlSource;
  lastSync: { rowCount: number; finishedAt: string } | undefined;
  onEdit: () => void;
}) {
  const sync = useSyncDataset();
  const disable = useDisableSqlSource();
  const enable = useEnableSqlSource();
  const del = useDeleteSqlSource();
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [creatingType, setCreatingType] = useState(false);

  const busy = sync.isPending || disable.isPending || enable.isPending || del.isPending;

  async function syncNow() {
    setResult(null);
    try {
      const res = await sync.mutateAsync(source.name);
      setResult({ ok: true, message: `${res.row_count} record${res.row_count === 1 ? "" : "s"} synced` });
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : "Sync failed" });
    }
  }

  async function toggleActive() {
    setResult(null);
    try {
      if (source.status === "active") await disable.mutateAsync(source.name);
      else await enable.mutateAsync(source.name);
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : "Couldn't update status" });
    }
  }

  return (
    <Card className={`hl-source-row${source.status === "disabled" ? " hl-source-row--disabled" : ""}`}>
      <div className="hl-source-row-header">
        <div>
          <strong>{source.name}</strong>
          <div className="hl-mono hl-text-muted-sm hl-mt-xs">
            {source.query ? source.query.slice(0, 80) + (source.query.length > 80 ? "…" : "") : source.table_name}
          </div>
          <div className="hl-tag-row hl-mt-xs">
            <Tag minimal icon="database">
              SQL
            </Tag>
            <Tag minimal icon="link">
              {source.connection_name}
            </Tag>
            {source.schedule_interval_minutes != null && (
              <Tag minimal icon="time">
                every {source.schedule_interval_minutes}min
              </Tag>
            )}
            {source.cursor_property && (
              <Tag minimal icon="fast-forward" title={`Incremental on ${source.cursor_property}`}>
                incremental
              </Tag>
            )}
          </div>
          <p className="hl-source-meta">
            {lastSync
              ? `Last synced ${new Date(lastSync.finishedAt).toLocaleString()} — ${lastSync.rowCount} record${lastSync.rowCount === 1 ? "" : "s"}`
              : "Never synced"}
            {source.schedule_interval_minutes == null
              ? " — manual only"
              : lastSync
                ? ` — next sync ~${nextSyncDescription(lastSync.finishedAt, source.schedule_interval_minutes)}`
                : " — will sync automatically shortly"}
          </p>
        </div>
        <div className="hl-source-row-actions">
          <Switch
            checked={source.status === "active"}
            label={source.status === "active" ? "Active" : "Disabled"}
            disabled={busy}
            onChange={() => void toggleActive()}
            className="hl-switch-reset"
          />
          <div className="hl-source-row-buttons">
            <Button small icon="cube-add" onClick={() => setCreatingType(true)} disabled={busy}>
              Create Object Type
            </Button>
            <Button small icon="edit" onClick={onEdit} disabled={busy}>
              Edit
            </Button>
            <Button small icon="refresh" loading={sync.isPending} disabled={source.status !== "active" || busy} onClick={() => void syncNow()}>
              Sync now
            </Button>
            <Button small icon="trash" intent="danger" minimal disabled={busy} onClick={() => setConfirmingDelete(true)} />
          </div>
        </div>
      </div>
      {result && (
        <p className={`hl-text-muted-sm hl-mt-sm ${result.ok ? "hl-text-success" : "hl-text-danger"}`}>{result.message}</p>
      )}
      <Alert
        isOpen={confirmingDelete}
        intent="danger"
        icon="trash"
        confirmButtonText="Delete"
        cancelButtonText="Cancel"
        onConfirm={() => void del.mutateAsync(source.name)}
        onCancel={() => setConfirmingDelete(false)}
      >
        <p>
          Delete <strong>{source.name}</strong>? Its configuration is removed permanently — already-synced Iceberg data
          is kept.
        </p>
      </Alert>
      {creatingType && <CreateObjectTypeDialog datasetName={source.name} onClose={() => setCreatingType(false)} />}
    </Card>
  );
}
