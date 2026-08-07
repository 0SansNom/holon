import { useState } from "react";
import { Button, Callout, Card, Dialog, DialogBody, DialogFooter, HTMLSelect, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { useRelationTypes, useCreateRelationType, useObjectTypes } from "../../api/hooks";
import { ApiError } from "../../api/client";

const CARDINALITIES = ["many_to_one", "one_to_many", "one_to_one", "many_to_many"] as const;

export function RelationTypesTab() {
  const { data, isLoading } = useRelationTypes();
  const { data: objectTypes = [] } = useObjectTypes();
  const createRelationType = useCreateRelationType();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [sourceObjectType, setSourceObjectType] = useState("");
  const [targetObjectType, setTargetObjectType] = useState("");
  const [sourceProperty, setSourceProperty] = useState("");
  const [cardinality, setCardinality] = useState<string>("many_to_one");
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setSourceObjectType("");
    setTargetObjectType("");
    setSourceProperty("");
    setCardinality("many_to_one");
    setError(null);
  }

  async function create() {
    setError(null);
    try {
      await createRelationType.mutateAsync({
        name,
        source_object_type: sourceObjectType,
        target_object_type: targetObjectType,
        source_property: sourceProperty,
        cardinality,
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
          A named, directional link between two ObjectTypes — the foreign-key property lives on the source side, the
          cardinality is spelled out explicitly, never implied.
        </p>
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New relation type
        </Button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
        {data?.map((rt) => (
          <Card key={rt.urn}>
            <strong
              style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              title={rt.name}
            >
              {rt.name}
            </strong>
            <div style={{ marginTop: 6, fontSize: 12, color: "var(--hl-text-muted)" }}>
              {rt.source_object_type_urn.split(":").pop()} —({rt.source_property})→ {rt.target_object_type_urn.split(":").pop()}
            </div>
            <Tag minimal style={{ marginTop: 6 }}>
              {rt.cardinality}
            </Tag>
          </Card>
        ))}
        {data?.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No relation types yet.</p>}
      </div>

      <Dialog
        isOpen={creating}
        onClose={() => {
          setCreating(false);
          reset();
        }}
        title="New relation type"
      >
        <DialogBody>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Name</label>
            <InputGroup placeholder="Order.customer" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Source ObjectType</label>
            <HTMLSelect fill value={sourceObjectType} onChange={(e) => setSourceObjectType(e.target.value)}>
              <option value="">Select…</option>
              {objectTypes.map((ot) => (
                <option key={ot.name} value={ot.name}>
                  {ot.name}
                </option>
              ))}
            </HTMLSelect>
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Target ObjectType</label>
            <HTMLSelect fill value={targetObjectType} onChange={(e) => setTargetObjectType(e.target.value)}>
              <option value="">Select…</option>
              {objectTypes.map((ot) => (
                <option key={ot.name} value={ot.name}>
                  {ot.name}
                </option>
              ))}
            </HTMLSelect>
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Source property (the foreign key)</label>
            <InputGroup placeholder="customerId" value={sourceProperty} onChange={(e) => setSourceProperty(e.target.value)} />
          </div>
          <div>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Cardinality</label>
            <HTMLSelect fill value={cardinality} onChange={(e) => setCardinality(e.target.value)} options={[...CARDINALITIES]} />
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
              disabled={!name || !sourceObjectType || !targetObjectType || !sourceProperty}
              loading={createRelationType.isPending}
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
