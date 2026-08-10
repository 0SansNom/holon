import { useMemo, useState } from "react";
import { useSources, useSyncs } from "../../api/hooks";
import type { GenericSource } from "../../api/connectivity";
import { EmptyState } from "../common/ListPrimitives";
import { OntologyTabHeader } from "../Ontology/OntologyTabLayout";
import { SourceRow } from "./SourceRow";
import { ConnectSourceDialog } from "./ConnectSourceDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";

export function DataSourcesTab() {
  const { data: sources } = useSources();
  const { data: syncs } = useSyncs();
  const [connecting, setConnecting] = useState(false);
  const [editingSource, setEditingSource] = useState<GenericSource | null>(null);

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
    </div>
  );
}
