import { useState } from "react";
import { Button, Callout, Card, Checkbox, Dialog, DialogBody, DialogFooter, FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import Editor from "@monaco-editor/react";
import { useMonacoEditorTheme } from "../../hooks/useMonacoEditorTheme";
import {
  useObjectTypes,
  useObjectTypeVersions,
  useProposeObjectTypeVersion,
  usePublishObjectTypeVersion,
  useInterfaces,
  useMarkings,
  useProjects,
  useObjectTypeGroups,
} from "../../api/hooks";
import type { ObjectType } from "../../api/knowledge";
import { ApiError } from "../../api/client";
import { CardGrid, EmptyState, ErrorCallout } from "../common/ListPrimitives";
import { ResourceActionsMenu, ResourceTagBadges } from "../common/ResourceActionsMenu";
import { BranchesDialog } from "./BranchesDialog";
import { OntologyTabHeader } from "./OntologyTabLayout";

function ManageObjectTypeDialog({ objectType, onClose }: { objectType: ObjectType; onClose: () => void }) {
  const monacoTheme = useMonacoEditorTheme();
  const { data: versions = [] } = useObjectTypeVersions(objectType.name);
  const { data: interfaces = [] } = useInterfaces();
  const { data: markings = [] } = useMarkings();
  const { data: projects = [] } = useProjects();
  const propose = useProposeObjectTypeVersion(objectType.name);
  const publish = usePublishObjectTypeVersion(objectType.name);

  const [description, setDescription] = useState(objectType.description);
  const [projectUrn, setProjectUrn] = useState(objectType.project_urn ?? "");
  const [implementsSet, setImplementsSet] = useState<Set<string>>(new Set(objectType.implements ?? []));
  const [markingsSet, setMarkingsSet] = useState<Set<string>>(new Set(objectType.markings ?? []));
  const [propertyTypesJson, setPropertyTypesJson] = useState(JSON.stringify(objectType.property_types ?? {}, null, 2));
  const [derivedPropertiesJson, setDerivedPropertiesJson] = useState(JSON.stringify(objectType.derived_properties ?? {}, null, 2));
  const [propertyFormatsJson, setPropertyFormatsJson] = useState(JSON.stringify(objectType.property_formats ?? {}, null, 2));
  const [conditionalFormatsJson, setConditionalFormatsJson] = useState(
    JSON.stringify(objectType.conditional_formats ?? {}, null, 2),
  );
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
    let property_types, derived_properties, property_formats, conditional_formats;
    try {
      property_types = JSON.parse(propertyTypesJson);
      derived_properties = JSON.parse(derivedPropertiesJson);
      property_formats = JSON.parse(propertyFormatsJson);
      conditional_formats = JSON.parse(conditionalFormatsJson);
    } catch {
      setError("Property types / derived properties / property formats / conditional formats must each be valid JSON.");
      return;
    }
    try {
      const draft = await propose.mutateAsync({
        description,
        implements: [...implementsSet],
        markings: [...markingsSet],
        property_types,
        derived_properties,
        property_formats,
        conditional_formats,
        project_urn: projectUrn || undefined,
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
        <div className="hl-mb-md">
          <div className="hl-section-title hl-mb-sm">Version history</div>
          <div className="hl-grid-gap-sm">
            {versions.map((v) => (
              <div key={v.id} className="hl-version-row">
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
            {versions.length === 0 && <p className="hl-text-muted">No versions proposed yet.</p>}
          </div>
        </div>

        <div className="hl-section-title hl-mb-sm">Propose a new version</div>
        <FormGroup label="Description">
          <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
        <FormGroup label="Project" helperText="Narrows access to this project's members, additive on top of workspace access">
          <HTMLSelect fill value={projectUrn} onChange={(e) => setProjectUrn(e.target.value)}>
            <option value="">No project</option>
            {projects.map((p) => (
              <option key={p.urn} value={p.urn}>
                {p.name}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>

        {interfaces.length > 0 && (
          <FormGroup label="Implements">
            {interfaces.map((iface) => (
              <Checkbox
                key={iface.name}
                label={iface.name}
                checked={implementsSet.has(iface.name)}
                onChange={() => toggle(implementsSet, setImplementsSet, iface.name)}
              />
            ))}
          </FormGroup>
        )}

        {markings.length > 0 && (
          <FormGroup label="Markings">
            {markings.map((m) => (
              <Checkbox
                key={m.name}
                label={m.name}
                checked={markingsSet.has(m.name)}
                onChange={() => toggle(markingsSet, setMarkingsSet, m.name)}
              />
            ))}
          </FormGroup>
        )}

        <FormGroup
          label="Property types"
          helperText={
            <span className="hl-mono">
              {"{"}property: {"{"}kind: "value_type"|"shared_property_type"|"struct"|"array", editable?, required?,
              ...{"}"}{"}"}
            </span>
          }
        >
          <div className="hl-json-editor">
            <Editor height="110px" defaultLanguage="json" theme={monacoTheme} value={propertyTypesJson} onChange={(v) => setPropertyTypesJson(v ?? "")} options={{ minimap: { enabled: false }, fontSize: 12 }} />
          </div>
        </FormGroup>
        <FormGroup
          label="Derived properties"
          helperText={
            <span className="hl-mono">
              {"{"}property: functionPluginName | {"{"}kind: "link_aggregate", relation, aggregate:
              "sum"|"count"|"avg"|"min"|"max", property?{"}"} | {"{"}kind: "struct_reducer", property, reducer:
              "first"|"last"|"latest"|"earliest"|"max"|"min", by?{"}"}{"}"}
            </span>
          }
        >
          <div className="hl-json-editor">
            <Editor height="90px" defaultLanguage="json" theme={monacoTheme} value={derivedPropertiesJson} onChange={(v) => setDerivedPropertiesJson(v ?? "")} options={{ minimap: { enabled: false }, fontSize: 12 }} />
          </div>
        </FormGroup>
        <FormGroup
          label="Property formats"
          helperText={
            <span className="hl-mono">
              {"{"}property: {"{"}kind: "currency"|"badge"|"numeric"|"datetime"|"principal"|"resource-link", ...{"}"}{"}"}
            </span>
          }
        >
          <div className="hl-json-editor">
            <Editor height="90px" defaultLanguage="json" theme={monacoTheme} value={propertyFormatsJson} onChange={(v) => setPropertyFormatsJson(v ?? "")} options={{ minimap: { enabled: false }, fontSize: 12 }} />
          </div>
        </FormGroup>
        <FormGroup
          label="Conditional formats"
          helperText={
            <span className="hl-mono">
              {"{"}property: [{"{"}condition: {"{"}type, ...{"}"}, style: {"{"}color, backgroundColor, textAlign{"}"}{"}"}, ...]{"}"}
            </span>
          }
        >
          <div className="hl-json-editor">
            <Editor height="90px" defaultLanguage="json" theme={monacoTheme} value={conditionalFormatsJson} onChange={(v) => setConditionalFormatsJson(v ?? "")} options={{ minimap: { enabled: false }, fontSize: 12 }} />
          </div>
        </FormGroup>

        {error && <ErrorCallout>{error}</ErrorCallout>}
        {ok && (
          <Callout intent="success" className="hl-mt-sm">
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
  const { data } = useObjectTypes();
  const { data: groups } = useObjectTypeGroups();
  const [managing, setManaging] = useState<ObjectType | null>(null);
  const [branching, setBranching] = useState<ObjectType | null>(null);
  const [groupFilter, setGroupFilter] = useState<string>("");

  const activeGroup = groups.find((g) => g.name === groupFilter);
  const visibleTypes = activeGroup ? data.filter((ot) => activeGroup.object_types.includes(ot.name)) : data;

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            ObjectTypes are created self-serve from a synced dataset (see the Sources page) — this is where the
            governed schema lifecycle lives: propose a versioned draft (typed properties, interfaces, markings, derived
            properties), then publish it to go live.
          </>
        }
        trailing={
          groups.length > 0 ? (
            <HTMLSelect value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)} className="hl-mr-sm">
              <option value="">All groups</option>
              {groups.map((g) => (
                <option key={g.name} value={g.name}>
                  {g.name}
                </option>
              ))}
            </HTMLSelect>
          ) : undefined
        }
      />

      <CardGrid minWidth={260}>
        {visibleTypes.map((ot) => (
          <Card key={ot.urn}>
            <div className="hl-registry-card-header">
              <strong className="hl-registry-card-title" title={ot.name}>
                {ot.name}
              </strong>
              <ResourceActionsMenu urn={ot.urn} />
            </div>
            <div className="hl-tag-row hl-mt-xs">
              <Tag minimal>{ot.classification}</Tag>
              <Tag minimal>v{ot.version}</Tag>
              {(ot.implements ?? []).map((i) => (
                <Tag key={i} minimal icon="link">
                  {i}
                </Tag>
              ))}
              <ResourceTagBadges urn={ot.urn} />
            </div>
            {ot.description && <p className="hl-card-desc">{ot.description}</p>}
            <div className="hl-card-actions">
              <Button small minimal icon="history" onClick={() => setManaging(ot)}>
                Versions
              </Button>
              <Button small minimal icon="git-branch" onClick={() => setBranching(ot)}>
                Branches
              </Button>
            </div>
          </Card>
        ))}
        {visibleTypes.length === 0 && (
          <EmptyState>{activeGroup ? "No ObjectTypes in this group." : "No ObjectTypes yet."}</EmptyState>
        )}
      </CardGrid>

      {managing && <ManageObjectTypeDialog objectType={managing} onClose={() => setManaging(null)} />}
      {branching && (
        <BranchesDialog
          kind="object_type"
          resourceName={branching.name}
          currentDefinition={{
            property_mapping: branching.property_mapping,
            description: branching.description,
            implements: branching.implements ?? [],
            derived_properties: branching.derived_properties ?? {},
            project_urn: branching.project_urn ?? null,
            markings: branching.markings ?? [],
            property_formats: branching.property_formats,
            conditional_formats: branching.conditional_formats ?? {},
            property_types: branching.property_types ?? {},
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
