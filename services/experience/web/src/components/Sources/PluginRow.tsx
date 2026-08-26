import { useState } from "react";
import { Button, Card, InputGroup, Switch, Tag } from "@blueprintjs/core";
import { useSyncDataset, useDisablePlugin, useEnablePlugin, useSetPluginSchedule } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { ConnectorPlugin } from "../../api/connectivity";
import { nextSyncDescription } from "./shared";
import { CreateObjectTypeDialog } from "./CreateObjectTypeDialog";

export function PluginRow({
  plugin,
  lastSync,
}: {
  plugin: ConnectorPlugin;
  lastSync: { rowCount: number; finishedAt: string } | undefined;
}) {
  const sync = useSyncDataset();
  const disable = useDisablePlugin();
  const enable = useEnablePlugin();
  const setSchedule = useSetPluginSchedule();
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [creatingType, setCreatingType] = useState(false);
  const [scheduleDraft, setScheduleDraft] = useState<string>(
    plugin.schedule_interval_minutes != null ? String(plugin.schedule_interval_minutes) : "",
  );

  const busy = sync.isPending || disable.isPending || enable.isPending || setSchedule.isPending;
  const datasetName = plugin.manifest.dataset_name ?? plugin.name;

  async function syncNow() {
    setResult(null);
    try {
      const res = await sync.mutateAsync(datasetName);
      setResult({ ok: true, message: `${res.row_count} record${res.row_count === 1 ? "" : "s"} synced` });
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : "Sync failed" });
    }
  }

  async function toggleActive() {
    setResult(null);
    try {
      if (plugin.status === "active") await disable.mutateAsync(plugin.name);
      else await enable.mutateAsync(plugin.name);
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : "Couldn't update status" });
    }
  }

  async function commitSchedule() {
    const minutes = scheduleDraft.trim() === "" ? null : Number(scheduleDraft);
    if (minutes === plugin.schedule_interval_minutes) return;
    setResult(null);
    try {
      await setSchedule.mutateAsync({ name: plugin.name, scheduleIntervalMinutes: minutes });
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : "Couldn't update schedule" });
    }
  }

  return (
    <Card className={`hl-source-row${plugin.status === "disabled" ? " hl-source-row--disabled" : ""}`}>
      <div className="hl-source-row-header">
        <div>
          <strong>{plugin.name}</strong>
          <div className="hl-mono hl-text-muted-sm hl-mt-xs">
            dataset: {datasetName} · v{plugin.version}
          </div>
          <div className="hl-tag-row hl-mt-xs">
            <Tag minimal icon="code-block">
              plugin
            </Tag>
            {plugin.schedule_interval_minutes != null && (
              <Tag minimal icon="time">
                every {plugin.schedule_interval_minutes}min
              </Tag>
            )}
          </div>
          <p className="hl-source-meta">
            {lastSync
              ? `Last synced ${new Date(lastSync.finishedAt).toLocaleString()} — ${lastSync.rowCount} record${lastSync.rowCount === 1 ? "" : "s"}`
              : "Never synced"}
            {plugin.schedule_interval_minutes == null
              ? " — manual only"
              : lastSync
                ? ` — next sync ~${nextSyncDescription(lastSync.finishedAt, plugin.schedule_interval_minutes)}`
                : " — will sync automatically shortly"}
          </p>
        </div>
        <div className="hl-source-row-actions">
          <Switch
            checked={plugin.status === "active"}
            label={plugin.status === "active" ? "Active" : "Disabled"}
            disabled={busy}
            onChange={() => void toggleActive()}
            className="hl-switch-reset"
          />
          <div className="hl-source-row-buttons">
            <Button small icon="cube-add" onClick={() => setCreatingType(true)} disabled={busy || !lastSync}>
              Create Object Type
            </Button>
            <InputGroup
              type="number"
              min={1}
              small
              placeholder="manual"
              value={scheduleDraft}
              onChange={(e) => setScheduleDraft(e.target.value)}
              onBlur={() => void commitSchedule()}
              disabled={busy}
              style={{ width: 84 }}
              title="Sync every ___ minutes — blank for manual only"
            />
            <Button
              small
              icon="refresh"
              loading={sync.isPending}
              disabled={plugin.status !== "active" || busy}
              onClick={() => void syncNow()}
            >
              Sync now
            </Button>
          </div>
        </div>
      </div>
      {result && (
        <p className={`hl-text-muted-sm hl-mt-sm ${result.ok ? "hl-text-success" : "hl-text-danger"}`}>{result.message}</p>
      )}
      {creatingType && <CreateObjectTypeDialog datasetName={datasetName} onClose={() => setCreatingType(false)} />}
    </Card>
  );
}
