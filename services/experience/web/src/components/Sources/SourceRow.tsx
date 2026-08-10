import { useState } from "react";
import { Alert, Button, Card, Switch, Tag } from "@blueprintjs/core";
import { useSyncDataset, useDisableSource, useEnableSource, useDeleteSource } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { GenericSource } from "../../api/connectivity";
import { nextSyncDescription } from "./shared";
import { CreateObjectTypeDialog } from "./CreateObjectTypeDialog";

export function SourceRow({
  source,
  lastSync,
  onEdit,
}: {
  source: GenericSource;
  lastSync: { rowCount: number; finishedAt: string } | undefined;
  onEdit: () => void;
}) {
  const sync = useSyncDataset();
  const disable = useDisableSource();
  const enable = useEnableSource();
  const del = useDeleteSource();
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
          <div className="hl-mono hl-text-muted-sm hl-mt-xs">{source.base_url}</div>
          <div className="hl-tag-row hl-mt-xs">
            {source.connection_name && <Tag minimal icon="link">{source.connection_name}</Tag>}
            {!source.connection_name && source.auth_header_name && <Tag minimal icon="key">{source.auth_header_name}</Tag>}
            {source.record_path && <Tag minimal icon="key-tab">{source.record_path}</Tag>}
            {source.next_page_path && <Tag minimal icon="numbered-list">paginated</Tag>}
            {source.schedule_interval_minutes != null && (
              <Tag minimal icon="time">
                every {source.schedule_interval_minutes}min
              </Tag>
            )}
            {source.cursor_property && source.incremental_param && (
              <Tag minimal icon="fast-forward" title={`Watches ${source.cursor_property}, sent as ${source.incremental_param}`}>
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
          Delete <strong>{source.name}</strong>? Its configuration is removed permanently — already-synced data in
          Iceberg is not affected, but you'd need to reconnect from scratch to sync it again.
        </p>
      </Alert>
      {creatingType && <CreateObjectTypeDialog source={source} onClose={() => setCreatingType(false)} />}
    </Card>
  );
}
