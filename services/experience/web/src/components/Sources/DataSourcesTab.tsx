import { useMemo, useState } from "react";
import { Button, Menu, MenuItem, PopoverNext } from "@blueprintjs/core";
import {
  useSources,
  usePlugins,
  useKafkaStreams,
  useSqlSources,
  useObjectSources,
  useSyncs,
} from "../../api/hooks";
import type { GenericSource, SqlSource, ObjectSource } from "../../api/connectivity";
import { EmptyState } from "../common/ListPrimitives";
import { OntologyTabHeader } from "../Ontology/OntologyTabLayout";
import { SourceRow } from "./SourceRow";
import { PluginRow } from "./PluginRow";
import { StreamRow } from "./StreamRow";
import { StreamDialog } from "./StreamDialog";
import { SqlSourceRow } from "./SqlSourceRow";
import { SqlSourceDialog } from "./SqlSourceDialog";
import { ObjectSourceRow } from "./ObjectSourceRow";
import { ObjectSourceDialog } from "./ObjectSourceDialog";
import { ConnectSourceDialog } from "./ConnectSourceDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";

export function DataSourcesTab() {
  const { data: sources } = useSources();
  const { data: plugins } = usePlugins();
  const { data: streams } = useKafkaStreams();
  const { data: sqlSources } = useSqlSources();
  const { data: objectSources } = useObjectSources();
  const { data: syncs } = useSyncs();
  const [connectingRest, setConnectingRest] = useState(false);
  const [connectingSql, setConnectingSql] = useState(false);
  const [connectingObject, setConnectingObject] = useState(false);
  const [editingSource, setEditingSource] = useState<GenericSource | null>(null);
  const [editingSqlSource, setEditingSqlSource] = useState<SqlSource | null>(null);
  const [editingObjectSource, setEditingObjectSource] = useState<ObjectSource | null>(null);
  const [addingStream, setAddingStream] = useState(false);

  usePaletteCreateIntent("connect-source", setConnectingRest);
  usePaletteCreateIntent("connect-sql-source", setConnectingSql);
  usePaletteCreateIntent("connect-object-source", setConnectingObject);
  usePaletteCreateIntent("connect-stream", setAddingStream);

  const lastSyncByName = useMemo(() => {
    const map = new Map<string, { rowCount: number; finishedAt: string }>();
    (syncs ?? []).forEach((run) => {
      const name = run.dataset_urn.split(":").pop();
      if (name && !map.has(name)) map.set(name, { rowCount: run.row_count, finishedAt: run.finished_at });
    });
    return map;
  }, [syncs]);

  const restDialogOpen = connectingRest || editingSource !== null;
  const sqlDialogOpen = connectingSql || editingSqlSource !== null;
  const objectDialogOpen = connectingObject || editingObjectSource !== null;

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Connect REST, SQL, object storage, Kafka, or registered plugins — no deploy. Synced data is catalogued the
            same way; map it to an Object Type under Admin.
          </>
        }
        trailing={
          <PopoverNext
            placement="bottom-end"
            content={
              <Menu>
                <MenuItem icon="globe-network" text="REST API" onClick={() => setConnectingRest(true)} />
                <MenuItem icon="database" text="SQL database" onClick={() => setConnectingSql(true)} />
                <MenuItem icon="cloud" text="Object storage" onClick={() => setConnectingObject(true)} />
                <MenuItem icon="pulse" text="Kafka stream" onClick={() => setAddingStream(true)} />
              </Menu>
            }
          >
            <Button intent="primary" icon="add" rightIcon="caret-down">
              Connect a source
            </Button>
          </PopoverNext>
        }
      />

      {plugins != null && plugins.length > 0 && (
        <>
          <div className="hl-section-title hl-mb-sm">Connectors</div>
          <p className="hl-text-muted-sm hl-mb-sm">
            Registered via the plugin SDK (Postgres, Mongo, CSV, …) — code, not this UI. Enable/disable, sync, and
            schedule are the same controls as other sources.
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
        Consumes a Kafka topic continuously — the latest message per key becomes a row, committed on its own schedule.
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

      <div className="hl-flex-between hl-mb-sm">
        <div className="hl-section-title">SQL sources</div>
        <Button small icon="add" minimal onClick={() => setConnectingSql(true)}>
          Connect SQL
        </Button>
      </div>
      <p className="hl-text-muted-sm hl-mb-sm">
        Read a whole table or a read-only SELECT from a registered SQL connection.
      </p>
      <div className="hl-source-list hl-mb-lg">
        {sqlSources?.map((s) => (
          <SqlSourceRow
            key={s.name}
            source={s}
            lastSync={lastSyncByName.get(s.name)}
            onEdit={() => setEditingSqlSource(s)}
          />
        ))}
        {sqlSources?.length === 0 && (
          <EmptyState actionLabel="Connect SQL" onAction={() => setConnectingSql(true)}>
            No SQL sources yet — add a SQL connection under the Connections tab first.
          </EmptyState>
        )}
      </div>

      <div className="hl-flex-between hl-mb-sm">
        <div className="hl-section-title">Object storage sources</div>
        <Button small icon="add" minimal onClick={() => setConnectingObject(true)}>
          Connect object storage
        </Button>
      </div>
      <p className="hl-text-muted-sm hl-mb-sm">
        Import CSV, NDJSON, or Parquet from a bucket — single object or all files under a prefix.
      </p>
      <div className="hl-source-list hl-mb-lg">
        {objectSources?.map((s) => (
          <ObjectSourceRow
            key={s.name}
            source={s}
            lastSync={lastSyncByName.get(s.name)}
            onEdit={() => setEditingObjectSource(s)}
          />
        ))}
        {objectSources?.length === 0 && (
          <EmptyState actionLabel="Connect object storage" onAction={() => setConnectingObject(true)}>
            No object sources yet — add an object connection under the Connections tab first.
          </EmptyState>
        )}
      </div>

      <div className="hl-section-title hl-mb-sm">REST sources</div>
      <div className="hl-source-list">
        {sources?.map((s) => (
          <SourceRow key={s.name} source={s} lastSync={lastSyncByName.get(s.name)} onEdit={() => setEditingSource(s)} />
        ))}
        {sources?.length === 0 && (
          <EmptyState actionLabel="Connect REST source" onAction={() => setConnectingRest(true)}>
            No REST sources connected yet.
          </EmptyState>
        )}
      </div>

      {restDialogOpen && (
        <ConnectSourceDialog
          editing={editingSource}
          onClose={() => {
            setConnectingRest(false);
            setEditingSource(null);
          }}
        />
      )}
      {sqlDialogOpen && (
        <SqlSourceDialog
          editing={editingSqlSource}
          onClose={() => {
            setConnectingSql(false);
            setEditingSqlSource(null);
          }}
        />
      )}
      {objectDialogOpen && (
        <ObjectSourceDialog
          editing={editingObjectSource}
          onClose={() => {
            setConnectingObject(false);
            setEditingObjectSource(null);
          }}
        />
      )}
      {addingStream && <StreamDialog onClose={() => setAddingStream(false)} />}
    </div>
  );
}
