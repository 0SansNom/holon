import { useState } from "react";
import { Button, Callout, Card, Checkbox, Dialog, DialogBody, DialogFooter, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import Editor from "@monaco-editor/react";
import {
  useObjectTypes,
  useObjectTypeVersions,
  useProposeObjectTypeVersion,
  usePublishObjectTypeVersion,
  useInterfaces,
  useMarkings,
} from "../../api/hooks";
import type { ObjectType } from "../../api/knowledge";
import { ApiError } from "../../api/client";

function ManageObjectTypeDialog({ objectType, onClose }: { objectType: ObjectType; onClose: () => void }) {
  const { data: versions = [] } = useObjectTypeVersions(objectType.name);
  const { data: interfaces = [] } = useInterfaces();
  const { data: markings = [] } = useMarkings();
  const propose = useProposeObjectTypeVersion(objectType.name);
  const publish = usePublishObjectTypeVersion(objectType.name);

  const [description, setDescription] = useState(objectType.description);
  const [implementsSet, setImplementsSet] = useState<Set<string>>(new Set(objectType.implements ?? []));
  const [markingsSet, setMarkingsSet] = useState<Set<string>>(new Set(objectType.markings ?? []));
  const [propertyTypesJson, setPropertyTypesJson] = useState(JSON.stringify(objectType.property_types ?? {}, null, 2));
  const [derivedPropertiesJson, setDerivedPropertiesJson] = useState(JSON.stringify(objectType.derived_properties ?? {}, null, 2));
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [publishingVersion, setPublishingVersion] = useState<number | null>(null);

  function toggle(set: Set<string>, setSet: (s: Set<string>) => void, value: string) {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    setSet(next);
  }

  async function proposeVersion() {
    setError(null);
    setOk(null);
    let property_types, derived_properties;
    try {
      property_types = JSON.parse(propertyTypesJson);
      derived_properties = JSON.parse(derivedPropertiesJson);
    } catch {
      setError("Property types / derived properties must each be valid JSON.");
      return;
    }
    try {
      const draft = await propose.mutateAsync({
        description,
        implements: [...implementsSet],
        markings: [...markingsSet],
        property_types,
        derived_properties,
      });
      setOk(`Proposed version ${draft.version} (draft) — publish it below when ready.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Propose failed");
    }
  }

  async function publishVersion(version: number) {
    setError(null);
    setOk(null);
    setPublishingVersion(version);
    try {
      await publish.mutateAsync(version);
      setOk(`Published version ${version}.`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Publish failed");
    } finally {
      setPublishingVersion(null);
    }
  }

  return (
    <Dialog isOpen title={`${objectType.name} — versions`} onClose={onClose} style={{ width: 620 }}>
      <DialogBody>
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: "var(--hl-text-muted)", marginBottom: 6 }}>Version history</div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {versions.map((v) => (
              <div key={v.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 10px", background: "var(--hl-surface-alt, rgba(255,255,255,0.03))", borderRadius: 4 }}>
                <span>
                  v{v.version} <Tag minimal intent={v.status === "published" ? "success" : "none"}>{v.status}</Tag>
                </span>
                {v.status === "draft" && (
                  <Button small loading={publishingVersion === v.version} onClick={() => void publishVersion(v.version)}>
                    Publish
                  </Button>
                )}
              </div>
            ))}
            {versions.length === 0 && <p style={{ color: "var(--hl-text-muted)", fontSize: 12 }}>No versions proposed yet.</p>}
          </div>
        </div>

        <div style={{ fontSize: 12, color: "var(--hl-text-muted)", marginBottom: 6 }}>Propose a new version</div>
        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Description</label>
          <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>

        {interfaces.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Implements</label>
            {interfaces.map((iface) => (
              <Checkbox
                key={iface.name}
                label={iface.name}
                checked={implementsSet.has(iface.name)}
                onChange={() => toggle(implementsSet, setImplementsSet, iface.name)}
              />
            ))}
          </div>
        )}

        {markings.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>Markings</label>
            {markings.map((m) => (
              <Checkbox
                key={m.name}
                label={m.name}
                checked={markingsSet.has(m.name)}
                onChange={() => toggle(markingsSet, setMarkingsSet, m.name)}
              />
            ))}
          </div>
        )}

        <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
          Property types{" "}
          <span className="hl-mono" style={{ color: "var(--hl-text-muted)" }}>
            {"{"}property: {"{"}kind: "value_type"|"shared_property_type"|"struct"|"array", ...{"}"}{"}"}
          </span>
        </label>
        <div className="hl-panel" style={{ padding: 0, overflow: "hidden", marginBottom: 12 }}>
          <Editor height="110px" defaultLanguage="json" theme="vs-dark" value={propertyTypesJson} onChange={(v) => setPropertyTypesJson(v ?? "")} options={{ minimap: { enabled: false }, fontSize: 12 }} />
        </div>
        <label style={{ fontSize: 12, display: "block", marginBottom: 4 }}>
          Derived properties <span className="hl-mono" style={{ color: "var(--hl-text-muted)" }}>{"{"}property: functionPluginName{"}"}</span>
        </label>
        <div className="hl-panel" style={{ padding: 0, overflow: "hidden" }}>
          <Editor height="70px" defaultLanguage="json" theme="vs-dark" value={derivedPropertiesJson} onChange={(v) => setDerivedPropertiesJson(v ?? "")} options={{ minimap: { enabled: false }, fontSize: 12 }} />
        </div>

        {error && (
          <Callout intent="danger" style={{ marginTop: 12 }}>
            {error}
          </Callout>
        )}
        {ok && (
          <Callout intent="success" style={{ marginTop: 12 }}>
            {ok}
          </Callout>
        )}
      </DialogBody>
      <DialogFooter
        actions={
          <Button intent="primary" loading={propose.isPending} onClick={() => void proposeVersion()}>
            Propose version
          </Button>
        }
      />
    </Dialog>
  );
}

export function ObjectTypesTab() {
  const { data, isLoading } = useObjectTypes();
  const [managing, setManaging] = useState<ObjectType | null>(null);

  if (isLoading) return <Spinner />;

  return (
    <div>
      <p style={{ fontSize: 12, color: "var(--hl-text-muted)", margin: "0 0 12px", maxWidth: 620 }}>
        ObjectTypes are created self-serve from a synced dataset (see the Sources page) — this is where the
        governed schema lifecycle lives: propose a versioned draft (typed properties, interfaces, markings, derived
        properties), then publish it to go live.
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
        {data?.map((ot) => (
          <Card key={ot.urn}>
            <strong
              style={{ display: "block", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
              title={ot.name}
            >
              {ot.name}
            </strong>
            <div style={{ marginTop: 6, display: "flex", gap: 6, flexWrap: "wrap" }}>
              <Tag minimal>{ot.classification}</Tag>
              <Tag minimal>v{ot.version}</Tag>
              {(ot.implements ?? []).map((i) => (
                <Tag key={i} minimal icon="link">
                  {i}
                </Tag>
              ))}
            </div>
            {ot.description && (
              <p style={{ fontSize: 12, color: "var(--hl-text-muted)", marginTop: 8, marginBottom: 8 }}>{ot.description}</p>
            )}
            <Button small minimal icon="history" onClick={() => setManaging(ot)}>
              Versions
            </Button>
          </Card>
        ))}
        {data?.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No ObjectTypes yet.</p>}
      </div>

      {managing && <ManageObjectTypeDialog objectType={managing} onClose={() => setManaging(null)} />}
    </div>
  );
}
