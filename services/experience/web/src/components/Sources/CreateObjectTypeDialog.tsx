import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import {
  Button,
  Callout,
  Dialog,
  DialogBody,
  DialogFooter,
  FormGroup,
  HTMLSelect,
  Icon,
  InputGroup,
} from "@blueprintjs/core";
import { useDatasetPreview, useCreateObjectType, useSyncDataset } from "../../api/hooks";
import { ApiError } from "../../api/client";
import { TENANT_ID, WORKSPACE_ID } from "../../api/config";
import type { GenericSource } from "../../api/connectivity";
import { SkeletonBlock } from "../common/Skeleton";
import { CLASSIFICATIONS, snakeToCamel, toPascalCase } from "./shared";

export function CreateObjectTypeDialog({ source, onClose }: { source: GenericSource; onClose: () => void }) {
  const { data: preview, isLoading: previewLoading, error: previewError } = useDatasetPreview(source.name);
  const [name, setName] = useState(toPascalCase(source.name));
  const [description, setDescription] = useState("");
  const [propertyNames, setPropertyNames] = useState<Record<string, string>>({});
  const [classifications, setClassifications] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<string | null>(null);

  const createType = useCreateObjectType();
  const sync = useSyncDataset();
  const busy = createType.isPending || sync.isPending;

  useEffect(() => {
    if (!preview) return;
    setPropertyNames((current) => {
      if (Object.keys(current).length > 0) return current;
      const suggested: Record<string, string> = {};
      preview.columns.forEach((c) => {
        suggested[c.name] = snakeToCamel(c.name);
      });
      return suggested;
    });
    setClassifications((current) => {
      if (Object.keys(current).length > 0) return current;
      const defaults: Record<string, string> = {};
      preview.columns.forEach((c) => {
        defaults[c.name] = "internal";
      });
      return defaults;
    });
  }, [preview]);

  const propertyMapping = useMemo(() => {
    const mapping: Record<string, string> = {};
    Object.entries(propertyNames).forEach(([column, property]) => {
      if (property.trim()) mapping[property.trim()] = column;
    });
    return mapping;
  }, [propertyNames]);

  async function create() {
    setError(null);
    try {
      const columnClassification: Record<string, string> = {};
      Object.keys(propertyMapping).forEach((property) => {
        const column = propertyMapping[property];
        columnClassification[column] = classifications[column] ?? "internal";
      });
      await createType.mutateAsync({
        name,
        source_dataset_urn: `hl:${TENANT_ID}:${WORKSPACE_ID}:dataset:${source.name}`,
        property_mapping: propertyMapping,
        description,
        column_classification: columnClassification,
      });
      await sync.mutateAsync(source.name);
      setCreated(name);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't create the Object Type");
    }
  }

  if (created) {
    return (
      <Dialog isOpen title="Create Object Type" onClose={onClose} style={{ width: 480 }}>
        <DialogBody>
          <div className="hl-dialog-success">
            <Icon icon="tick-circle" size={36} intent="success" />
            <p className="hl-dialog-success-title">Created</p>
            <p className="hl-dialog-success-text">
              <span className="hl-mono">{created}</span> is browsable now, under Objects.
            </p>
          </div>
        </DialogBody>
        <DialogFooter
          actions={
            <>
              <Button onClick={onClose}>Close</Button>
              <Link to="/objects/$type" params={{ type: created }}>
                <Button intent="primary" onClick={onClose}>
                  View {created}
                </Button>
              </Link>
            </>
          }
        />
      </Dialog>
    );
  }

  return (
    <Dialog isOpen title="Create Object Type" onClose={onClose} style={{ width: 580 }}>
      <DialogBody>
        <p className="hl-dialog-desc">
          Turn <span className="hl-mono">{source.name}</span>'s synced data into a real, browsable Object Type —
          name its properties below, suggested from the columns actually in the data.
        </p>
        <FormGroup label="Object Type name">
          <InputGroup value={name} onChange={(e) => setName(e.target.value)} placeholder="MyObjectType" />
        </FormGroup>
        <FormGroup label="Description (optional)">
          <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What is this?" />
        </FormGroup>

        {previewLoading && (
          <div aria-busy aria-label="Loading columns">
            {Array.from({ length: 4 }, (_, i) => (
              <SkeletonBlock key={i} width="100%" height={28} className="hl-mb-xs" />
            ))}
          </div>
        )}
        {previewError && (
          <Callout intent="warning" icon="warning-sign">
            Couldn't read this source's columns — sync it at least once first (use "Sync now" on its row), then try
            again.
          </Callout>
        )}
        {preview && preview.columns.length > 0 && (
          <>
            <div className="hl-mapping-grid hl-mapping-grid-header">
              <span>Column</span>
              <span>Property</span>
              <span>Sensitivity</span>
            </div>
            <div className="hl-mapping-grid">
              {preview.columns.map((c) => (
                <Fragment key={c.name}>
                  <span className="hl-mono hl-text-muted">{c.name}</span>
                  <InputGroup
                    small
                    value={propertyNames[c.name] ?? ""}
                    onChange={(e) => setPropertyNames((p) => ({ ...p, [c.name]: e.target.value }))}
                    placeholder="(skip this column)"
                  />
                  <HTMLSelect
                    fill
                    minimal
                    disabled={!propertyNames[c.name]?.trim()}
                    value={classifications[c.name] ?? "internal"}
                    onChange={(e) => setClassifications((cls) => ({ ...cls, [c.name]: e.target.value }))}
                  >
                    {CLASSIFICATIONS.map((level) => (
                      <option key={level} value={level}>
                        {level}
                      </option>
                    ))}
                  </HTMLSelect>
                </Fragment>
              ))}
            </div>
            <p className="hl-mapping-hint">
              "Confidential"/"restricted" columns are actually masked from principals without clearance — not just a
              label, enforced on every read.
            </p>
          </>
        )}

        {error && (
          <Callout intent="danger" className="hl-mt-sm" title="Couldn't create">
            {error}
          </Callout>
        )}
      </DialogBody>
      <DialogFooter
        actions={
          <>
            <Button onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            <Button
              intent="primary"
              loading={busy}
              disabled={!name || !preview || preview.columns.length === 0 || Object.keys(propertyMapping).length === 0}
              onClick={() => void create()}
            >
              Create
            </Button>
          </>
        }
      />
    </Dialog>
  );
}
