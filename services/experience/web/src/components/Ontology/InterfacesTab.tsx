import { useState } from "react";
import { Button, Callout, Card, Dialog, DialogBody, DialogFooter, InputGroup, Spinner, Tag, TagInput } from "@blueprintjs/core";
import { useInterfaces, useCreateInterface } from "../../api/hooks";
import { ApiError } from "../../api/client";

export function InterfacesTab() {
  const { data, isLoading } = useInterfaces();
  const createInterface = useCreateInterface();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [requiredProperties, setRequiredProperties] = useState<string[]>([]);
  const [requiredActions, setRequiredActions] = useState<string[]>([]);
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setName("");
    setRequiredProperties([]);
    setRequiredActions([]);
    setDescription("");
    setError(null);
  }

  async function create() {
    setError(null);
    try {
      await createInterface.mutateAsync({
        name,
        required_properties: requiredProperties,
        required_actions: requiredActions,
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
          A named, checked contract — an ObjectType declaring <code>implements</code> must actually have every
          required property mapped and every required Action defined, checked at publish time, not just a label.
        </p>
        <Button intent="primary" icon="add" onClick={() => setCreating(true)}>
          New interface
        </Button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
        {data?.map((iface) => (
          <Card key={iface.name}>
            <strong
              style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              title={iface.name}
            >
              {iface.name}
            </strong>
            {iface.required_properties.length > 0 && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 11, color: "var(--hl-text-muted)" }}>Requires properties</div>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 4 }}>
                  {iface.required_properties.map((p) => (
                    <Tag key={p} minimal>
                      {p}
                    </Tag>
                  ))}
                </div>
              </div>
            )}
            {iface.required_actions.length > 0 && (
              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: 11, color: "var(--hl-text-muted)" }}>Requires actions</div>
                <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginTop: 4 }}>
                  {iface.required_actions.map((a) => (
                    <Tag key={a} minimal icon="lightning">
                      {a}
                    </Tag>
                  ))}
                </div>
              </div>
            )}
            {iface.description && (
              <p style={{ fontSize: 12, color: "var(--hl-text-muted)", marginTop: 8, marginBottom: 0 }}>{iface.description}</p>
            )}
          </Card>
        ))}
        {data?.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No interfaces yet.</p>}
      </div>

      <Dialog
        isOpen={creating}
        onClose={() => {
          setCreating(false);
          reset();
        }}
        title="New interface"
      >
        <DialogBody>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Name</label>
            <InputGroup placeholder="Contactable" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Required properties</label>
            <TagInput
              placeholder="type a property name, press Enter"
              values={requiredProperties}
              onChange={(values) => setRequiredProperties(values as string[])}
            />
          </div>
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Required actions</label>
            <TagInput
              placeholder="type an action's local name, press Enter"
              values={requiredActions}
              onChange={(values) => setRequiredActions(values as string[])}
            />
          </div>
          <div>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Description</label>
            <InputGroup placeholder="Anything with a reachable contact method" value={description} onChange={(e) => setDescription(e.target.value)} />
          </div>
          {error && (
            <Callout intent="danger" style={{ marginTop: 12 }}>
              {error}
            </Callout>
          )}
        </DialogBody>
        <DialogFooter
          actions={
            <Button intent="primary" disabled={!name} loading={createInterface.isPending} onClick={() => void create()}>
              Create
            </Button>
          }
        />
      </Dialog>
    </div>
  );
}
