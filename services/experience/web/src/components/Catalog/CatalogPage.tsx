import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearch } from "@tanstack/react-router";
import { Button, Callout, Spinner, Tag } from "@blueprintjs/core";
import { useDatasetPreview, useDatasetStats, useDatasetVersions, useDatasets, useObjectTypes } from "../../api/hooks";
import type { CatalogDataset, ObjectType } from "../../api/knowledge";
import { EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { urnShortName } from "../ObjectExplorer/objectExplorerUtils";

function formatWhen(value: string): string {
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function DatasetDetail({
  dataset,
  mappedTypes,
}: {
  dataset: CatalogDataset;
  mappedTypes: ObjectType[];
}) {
  const navigate = useNavigate();
  const { data: preview, isLoading, error } = useDatasetPreview(dataset.display_name);
  const [showStats, setShowStats] = useState(false);
  const [showVersions, setShowVersions] = useState(false);
  const { data: stats, isLoading: statsLoading } = useDatasetStats(dataset.display_name, showStats);
  const { data: versions, isLoading: versionsLoading } = useDatasetVersions(dataset.display_name, showVersions);

  return (
    <div className="hl-panel hl-mt-md">
      <div className="hl-flex-between hl-mb-md">
        <div>
          <div className="hl-section-title">{dataset.display_name}</div>
          <div className="hl-mono hl-text-muted-sm hl-mt-xs">{dataset.urn}</div>
        </div>
        <div className="hl-flex-row hl-gap-sm">
          <Button
            icon="flow-linear"
            intent="primary"
            onClick={() =>
              void navigate({
                to: "/lineage/$urn",
                params: { urn: dataset.latest_version_urn },
              })
            }
          >
            View lineage
          </Button>
          {mappedTypes.map((ot) => (
            <Button
              key={ot.name}
              icon="cube"
              onClick={() => void navigate({ to: "/objects/$type", params: { type: ot.name } })}
            >
              Open {ot.name}
            </Button>
          ))}
        </div>
      </div>

      <div className="hl-tag-row hl-mb-md">
        <Tag minimal>{dataset.row_count.toLocaleString()} rows</Tag>
        <Tag minimal>snapshot {String(dataset.snapshot_id)}</Tag>
        <Tag minimal>{formatWhen(dataset.created_at)}</Tag>
        {mappedTypes.length === 0 && (
          <Tag minimal intent="warning">
            unmapped
          </Tag>
        )}
        {mappedTypes.map((ot) => (
          <Tag key={ot.name} minimal intent="primary">
            → {ot.name}
          </Tag>
        ))}
      </div>

      <div className="hl-section-title hl-mb-sm">Column preview</div>
      {isLoading && (
        <div className="hl-flex-row hl-items-center hl-gap-sm">
          <Spinner size={16} />
          <span className="hl-text-muted-sm">Loading preview…</span>
        </div>
      )}
      {error && (
        <Callout intent="warning" className="hl-mb-sm">
          {(error as Error).message || "Preview unavailable — sync the source first."}
        </Callout>
      )}
      {preview && preview.columns.length > 0 && (
        <div className="hl-table-scroll">
          <table className="hl-data-table hl-data-table-compact">
            <thead>
              <tr>
                <th>Column</th>
                <th>Sample</th>
              </tr>
            </thead>
            <tbody>
              {preview.columns.map((col) => (
                <tr key={col.name} className="hl-data-table-row">
                  <td className="hl-mono">{col.name}</td>
                  <td className="hl-mono hl-text-muted-sm">{String(col.sample ?? "—")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {preview && preview.columns.length === 0 && (
        <p className="hl-text-muted-sm">Dataset has no rows to preview.</p>
      )}

      <div className="hl-flex-between hl-mt-md hl-mb-sm">
        <div className="hl-section-title">Schema & stats</div>
        <Button small minimal icon={showStats ? "chevron-up" : "chevron-down"} onClick={() => setShowStats((s) => !s)}>
          {showStats ? "Hide" : "Show"}
        </Button>
      </div>
      {showStats && (
        <>
          {statsLoading && (
            <div className="hl-flex-row hl-items-center hl-gap-sm">
              <Spinner size={16} />
              <span className="hl-text-muted-sm">Scanning {dataset.row_count.toLocaleString()} rows…</span>
            </div>
          )}
          {stats && stats.columns.length > 0 && (
            <div className="hl-table-scroll">
              <table className="hl-data-table hl-data-table-compact">
                <thead>
                  <tr>
                    <th>Column</th>
                    <th>Type</th>
                    <th>Nulls</th>
                    <th>Distinct</th>
                    <th>Min</th>
                    <th>Max</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.columns.map((col) => (
                    <tr key={col.name} className="hl-data-table-row">
                      <td className="hl-mono">{col.name}</td>
                      <td className="hl-mono hl-text-muted-sm">{col.type}</td>
                      <td>{col.null_count ?? "—"}</td>
                      <td>{col.distinct_count ?? "—"}</td>
                      <td className="hl-mono hl-text-muted-sm">{col.min ?? "—"}</td>
                      <td className="hl-mono hl-text-muted-sm">{col.max ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      <div className="hl-flex-between hl-mt-md hl-mb-sm">
        <div className="hl-section-title">Snapshot history</div>
        <Button
          small
          minimal
          icon={showVersions ? "chevron-up" : "chevron-down"}
          onClick={() => setShowVersions((s) => !s)}
        >
          {showVersions ? "Hide" : "Show"}
        </Button>
      </div>
      {showVersions && (
        <>
          {versionsLoading && (
            <div className="hl-flex-row hl-items-center hl-gap-sm">
              <Spinner size={16} />
              <span className="hl-text-muted-sm">Loading history…</span>
            </div>
          )}
          {versions && versions.length > 0 && (
            <div className="hl-table-scroll">
              <table className="hl-data-table hl-data-table-compact">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Snapshot</th>
                    <th>Rows</th>
                  </tr>
                </thead>
                <tbody>
                  {versions.map((v) => (
                    <tr key={v.urn} className="hl-data-table-row">
                      <td className="hl-text-muted-sm">{formatWhen(v.created_at)}</td>
                      <td className="hl-mono hl-text-muted-sm">{String(v.snapshot_id)}</td>
                      <td>{v.row_count.toLocaleString()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {versions && versions.length === 0 && <p className="hl-text-muted-sm">No history recorded.</p>}
        </>
      )}

      <p className="hl-text-muted-sm hl-mt-md hl-mono" style={{ wordBreak: "break-all" }}>
        location: {dataset.location || "—"}
        <br />
        version: {dataset.latest_version_urn}
      </p>
    </div>
  );
}

export function CatalogPage() {
  const { dataset: preselect } = useSearch({ from: "/shell/catalog" });
  const navigate = useNavigate();
  const { data: datasets } = useDatasets();
  const { data: objectTypes } = useObjectTypes();
  const [selectedUrn, setSelectedUrn] = useState<string | null>(null);

  const otsByDatasetUrn = useMemo(() => {
    const map = new Map<string, ObjectType[]>();
    for (const ot of objectTypes ?? []) {
      if (!ot.source_dataset_urn) continue;
      const list = map.get(ot.source_dataset_urn) ?? [];
      list.push(ot);
      map.set(ot.source_dataset_urn, list);
    }
    return map;
  }, [objectTypes]);

  useEffect(() => {
    if (preselect) {
      const match = (datasets ?? []).find(
        (d) => d.display_name === preselect || urnShortName(d.urn) === preselect,
      );
      if (match) setSelectedUrn(match.urn);
      return;
    }
    if (!selectedUrn && (datasets ?? []).length > 0) {
      setSelectedUrn(datasets[0].urn);
    }
  }, [preselect, datasets, selectedUrn]);

  const selected = (datasets ?? []).find((d) => d.urn === selectedUrn) ?? null;
  const mappedForSelected = selected ? (otsByDatasetUrn.get(selected.urn) ?? []) : [];

  return (
    <RegistryPage
      title="Catalog"
      description={
        <>
          Governed datasets from Connectivity syncs (Iceberg). Map each dataset to an ObjectType to serve instances;
          lineage hangs off the latest dataset version, not the bare dataset URN.
        </>
      }
    >
      {(datasets ?? []).length === 0 && (
        <EmptyState>No catalogued datasets yet — sync a source from Sources first.</EmptyState>
      )}

      {(datasets ?? []).length > 0 && (
        <>
          <div className="hl-panel hl-table-scroll">
            <table className="hl-data-table">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Rows</th>
                  <th>Object types</th>
                  <th>Last version</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {(datasets ?? []).map((ds) => {
                  const mapped = otsByDatasetUrn.get(ds.urn) ?? [];
                  const active = ds.urn === selectedUrn;
                  return (
                    <tr
                      key={ds.urn}
                      className="hl-data-table-row"
                      data-selected={active}
                      onClick={() => setSelectedUrn(ds.urn)}
                      style={{ cursor: "pointer" }}
                    >
                      <td>
                        <strong>{ds.display_name}</strong>
                        <div className="hl-mono hl-text-muted-sm">{ds.urn}</div>
                      </td>
                      <td>{ds.row_count.toLocaleString()}</td>
                      <td>
                        {mapped.length === 0 && <span className="hl-text-muted">—</span>}
                        <span className="hl-flex-row hl-gap-xs" style={{ flexWrap: "wrap" }}>
                          {mapped.map((ot) => (
                            <Link
                              key={ot.name}
                              to="/objects/$type"
                              params={{ type: ot.name }}
                              className="hl-link-accent"
                              onClick={(e) => e.stopPropagation()}
                            >
                              {ot.name}
                            </Link>
                          ))}
                        </span>
                      </td>
                      <td className="hl-text-muted-sm">{formatWhen(ds.created_at)}</td>
                      <td>
                        <Button
                          icon="flow-linear"
                          title="View lineage"
                          onClick={(e) => {
                            e.stopPropagation();
                            void navigate({
                              to: "/lineage/$urn",
                              params: { urn: ds.latest_version_urn },
                            });
                          }}
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {selected && <DatasetDetail key={selected.urn} dataset={selected} mappedTypes={mappedForSelected} />}
        </>
      )}
    </RegistryPage>
  );
}
