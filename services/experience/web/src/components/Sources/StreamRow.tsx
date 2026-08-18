import { useState } from "react";
import { Alert, Button, Card, Switch, Tag } from "@blueprintjs/core";
import { useDisableKafkaStream, useEnableKafkaStream, useDeleteKafkaStream } from "../../api/hooks";
import { ApiError } from "../../api/client";
import type { KafkaStreamSource } from "../../api/connectivity";

export function StreamRow({ stream }: { stream: KafkaStreamSource }) {
  const disable = useDisableKafkaStream();
  const enable = useEnableKafkaStream();
  const del = useDeleteKafkaStream();
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const busy = disable.isPending || enable.isPending || del.isPending;

  async function toggleActive() {
    setResult(null);
    try {
      if (stream.status === "active") await disable.mutateAsync(stream.name);
      else await enable.mutateAsync(stream.name);
    } catch (err) {
      setResult({ ok: false, message: err instanceof ApiError ? err.message : "Couldn't update status" });
    }
  }

  return (
    <Card className={`hl-source-row${stream.status === "disabled" ? " hl-source-row--disabled" : ""}`}>
      <div className="hl-source-row-header">
        <div>
          <strong>{stream.name}</strong>
          <div className="hl-mono hl-text-muted-sm hl-mt-xs">
            topic: {stream.topic} · dataset: {stream.dataset_name}
          </div>
          <div className="hl-tag-row hl-mt-xs">
            <Tag minimal icon="feed">
              streaming
            </Tag>
            <Tag minimal icon="key" title="The JSON field treated as each record's unique key">
              key: {stream.key_field}
            </Tag>
            <Tag minimal icon="time">
              every {stream.batch_interval_seconds}s
            </Tag>
          </div>
        </div>
        <div className="hl-source-row-actions">
          <Switch
            checked={stream.status === "active"}
            label={stream.status === "active" ? "Consuming" : "Disabled"}
            disabled={busy}
            onChange={() => void toggleActive()}
            className="hl-switch-reset"
          />
          <div className="hl-source-row-buttons">
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
        onConfirm={() => void del.mutateAsync(stream.name)}
        onCancel={() => setConfirmingDelete(false)}
      >
        <p>
          Delete <strong>{stream.name}</strong>? Its consumer stops immediately — already-catalogued data in Iceberg
          is not affected, but you'd need to re-register to resume consuming this topic.
        </p>
      </Alert>
    </Card>
  );
}
