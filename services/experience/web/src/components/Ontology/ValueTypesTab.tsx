import { useState } from "react";
import { Button, Callout, Card, Dialog, DialogBody, DialogFooter, HTMLSelect, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { useValueTypes, useCreateValueType } from "../../api/hooks";
import { ApiError } from "../../api/client";

const BASE_TYPES = ["string", "integer", "double", "boolean", "date", "timestamp"] as const;

export function ValueTypesTab() {
  const { data, isLoading } = useValueTypes();
  const createValueType = useCreateValueType();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [baseType, setBaseType] = useState<string>("string");
  const [formatRegex, setFormatRegex] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setBaseType("string");
    setFormatRegex("");
    setDescription("");
    setError(null);
  }

  async function create() {
    setError(null);
    try {
      await createValueType.mutateAsync({
        name,
        base_type: baseType,
        format_regex: baseType === "string" && formatRegex ? formatRegex : undefined,
        description: description || undefined,
      });
      setCreating(false);
      reset();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Create failed");
    }
  }

  if (isLoading) return <Spinner />;

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)", margin: 0, maxWidth: 560 }}>
          A named, reusable data type — a base primitive plus an optional format constraint (e.g. "Email" = string +
          a regex). Referenced by a typed property (<code>property_types</code>) or a declarative Action's parameter.
        </p>
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New value type
        </Button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
        {data?.map((vt) => (
          <Card key={vt.name}>
            <strong
              style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              title={vt.name}
            >
              {vt.name}
            </strong>
            <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Tag minimal>{vt.base_type}</Tag>
              {vt.format_regex && (
                <Tag minimal icon="regex" className="hl-mono">
                  {vt.format_regex}
                </Tag>
              )}
            </div>
            {vt.description && (
              <p style={{ fontSize: 12, color: "var(--hl-text-muted)", marginTop: 8, marginBottom: 0 }}>{vt.description}</p>
            )}
          </Card>
        ))}
        {data?.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No value types yet.</p>}
      </div>

      <Dialog
        isOpen={creating}
        onClose={() => {
          setCreating(false);
          reset();
        }}
        title="New value type"
      >
        <DialogBody>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Name</label>
            <InputGroup placeholder="Email" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Base type</label>
            <HTMLSelect fill value={baseType} onChange={(e) => setBaseType(e.target.value)} options={[...BASE_TYPES]} />
          </div>
          {baseType === "string" && (
            <div style={{ marginBottom: 12 }}>
              <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Format regex (optional)</label>
              <InputGroup
                className="hl-mono"
                placeholder="^[^@]+@[^@]+\.[^@]+$"
                value={formatRegex}
                onChange={(e) => setFormatRegex(e.target.value)}
              />
            </div>
          )}
          <div>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Description</label>
            <InputGroup placeholder="an email address" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          {error && (
            <Callout intent="danger" style={{ marginTop: 12 }}>
              {error}
            </Callout>
          )}
        </DialogBody>
        <DialogFooter
          actions={
            <Button intent="primary" disabled={!name} loading={createValueType.isPending} onClick={() => void create()}>
              Create
            </Button>
          }
        />
      </Dialog>
    </div>
  );
}
