import { useState } from "react";
import { Button, Callout, Card, Dialog, DialogBody, DialogFooter, HTMLSelect, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { useSharedPropertyTypes, useCreateSharedPropertyType, useValueTypes } from "../../api/hooks";
import { ApiError } from "../../api/client";

export function SharedPropertyTypesTab() {
  const { data, isLoading } = useSharedPropertyTypes();
  const { data: valueTypes = [] } = useValueTypes();
  const createSharedPropertyType = useCreateSharedPropertyType();
  const [creating, setCreating] = useState(false);
  const [apiName, setApiName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [valueType, setValueType] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setApiName("");
    setDisplayName("");
    setValueType("");
    setDescription("");
    setError(null);
  }

  async function create() {
    setError(null);
    try {
      await createSharedPropertyType.mutateAsync({
        api_name: apiName,
        display_name: displayName,
        value_type: valueType,
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
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)", margin: 0, maxWidth: 620 }}>
          A canonical, reusable <em>property</em> definition — an API name plus a display name and description,
          wrapping a Value Type for its data shape. Reference it from any ObjectType's <code>property_types</code>{" "}
          (<code>{"{kind: \"shared_property_type\", shared_property_type: \"…\"}"}</code>) so renaming or
          redescribing the property is a single edit, not one per ObjectType.
        </p>
        <Button intent="primary" icon="add" onClick={() => setCreating(true)} disabled={valueTypes.length === 0}>
          New shared property type
        </Button>
      </div>

      {valueTypes.length === 0 && (
        <Callout intent="none" style={{ marginBottom: 12 }}>
          Register a Value Type first (the "Value Types" tab) — a Shared Property Type always wraps one.
        </Callout>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
        {data?.map((spt) => (
          <Card key={spt.api_name}>
            <strong
              style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              title={spt.display_name}
            >
              {spt.display_name}
            </strong>
            <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Tag minimal className="hl-mono">
                {spt.api_name}
              </Tag>
              <Tag minimal icon="link">
                {spt.value_type}
              </Tag>
            </div>
            {spt.description && (
              <p style={{ fontSize: 12, color: "var(--hl-text-muted)", marginTop: 8, marginBottom: 0 }}>{spt.description}</p>
            )}
          </Card>
        ))}
        {data?.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No shared property types yet.</p>}
      </div>

      <Dialog
        isOpen={creating}
        onClose={() => {
          setCreating(false);
          reset();
        }}
        title="New shared property type"
      >
        <DialogBody>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
              API name <span className="hl-mono" style={{ color: "var(--hl-text-muted)" }}>(referenced by property_types)</span>
            </label>
            <InputGroup className="hl-mono" placeholder="email" value={apiName} onChange={(e) => setApiName(e.target.value)} />
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Display name</label>
            <InputGroup placeholder="Email address" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Value type</label>
            <HTMLSelect fill value={valueType} onChange={(e) => setValueType(e.target.value)}>
              <option value="">Select…</option>
              {valueTypes.map((vt) => (
                <option key={vt.name} value={vt.name}>
                  {vt.name}
                </option>
              ))}
            </HTMLSelect>
          </div>
          <div>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Description</label>
            <InputGroup
              placeholder="the canonical contact email property"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
          </div>
          {error && (
            <Callout intent="danger" style={{ marginTop: 12 }}>
              {error}
            </Callout>
          )}
        </DialogBody>
        <DialogFooter
          actions={
            <Button
              intent="primary"
              disabled={!apiName || !displayName || !valueType}
              loading={createSharedPropertyType.isPending}
              onClick={() => void create()}
            >
              Create
            </Button>
          }
        />
      </Dialog>
    </div>
  );
}
