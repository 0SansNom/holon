import { useMemo, useState } from "react";
import { Button } from "@blueprintjs/core";
import { useSources, usePlugins, useKafkaStreams, useSyncs } from "../../api/hooks";
import type { GenericSource } from "../../api/connectivity";
import { EmptyState } from "../common/ListPrimitives";
import { OntologyTabHeader } from "../Ontology/OntologyTabLayout";
import { SourceRow } from "./SourceRow";
import { PluginRow } from "./PluginRow";
import { StreamRow } from "./StreamRow";
import { StreamDialog } from "./StreamDialog";
import { ConnectSourceDialog } from "./ConnectSourceDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";

export function DataSourcesTab() {
  const { data: sources } = useSources();
  const { data: plugins } = usePlugins();
  const { data: streams } = useKafkaStreams();
  const { data: syncs } = useSyncs();
  const [connecting, setConnecting] = useState(false);
  const [editingSource, setEditingSource] = useState<GenericSource | null>(null);
  const [addingStream, setAddingStream] = useState(false);

  usePaletteCreateIntent("connect-source", setConnecting);

  const lastSyncByName = useMemo(() => {
    const map = new Map<string, { rowCount: number; finishedAt: string }>();
    (syncs ?? []).forEach((run) => {
      const name = run.dataset_urn.split(":").pop();
      if (name && !map.has(name)) map.set(name, { rowCount: run.row_count, finishedAt: run.finished_at });
    });
    return map;
  }, [syncs]);

  const dialogOpen = connecting || editingSource !== null;

  function closeDialog() {
    setConnecting(false);
    setEditingSource(null);
  }

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Connect any JSON REST API by URL — no code, no deploy. Once synced, a source's data is catalogued the same
            way as every built-in connector; mapping it to an Object Type is the next step, under Admin.
          </>
        }
        createLabel="Connect a source"
        onCreate={() => setConnecting(true)}
      />

      {plugins != null && plugins.length > 0 && (
        <>
          <div className="hl-section-title hl-mb-sm">Connectors</div>
          <p className="hl-text-muted-sm hl-mb-sm">
            Registered via the plugin SDK (Postgres, Mongo, CSV, …) — code, not this UI. Enable/disable, sync, and
            schedule are the same controls as a REST source.
          </p>
          <div className="hl-source-list hl-mb-lg">
            {plugins.map((p) => (
              <PluginRow key={p.name} plugin={p} lastSync={lastSyncByName.get(p.manifest.dataset_name ?? p.name)} />
            ))}
          </div>
        </>
      )}

      <div className="hl-flex-between hl-mb-sm">
        <div className="hl-section-title">Streaming</div>
        <Button small icon="add" minimal onClick={() => setAddingStream(true)}>
          New stream
        </Button>
      </div>
      <p className="hl-text-muted-sm hl-mb-sm">
        Consumes a Kafka topic continuously — the latest message per key becomes a row, committed on its own
        schedule, no manual sync.
      </p>
      <div className="hl-source-list hl-mb-lg">
        {streams?.map((s) => (
          <StreamRow key={s.name} stream={s} />
        ))}
        {streams?.length === 0 && (
          <EmptyState actionLabel="New stream" onAction={() => setAddingStream(true)}>
            No streams registered yet.
          </EmptyState>
        )}
      </div>

      <div className="hl-section-title hl-mb-sm">REST sources</div>
      <div className="hl-source-list">
        {sources?.map((s) => (
          <SourceRow key={s.name} source={s} lastSync={lastSyncByName.get(s.name)} onEdit={() => setEditingSource(s)} />
        ))}
        {sources?.length === 0 && (
          <EmptyState actionLabel="Connect a source" onAction={() => setConnecting(true)}>
            No sources connected yet.
          </EmptyState>
        )}
      </div>

      {dialogOpen && <ConnectSourceDialog editing={editingSource} onClose={closeDialog} />}
      {addingStream && <StreamDialog onClose={() => setAddingStream(false)} />}
    </div>
  );
}
