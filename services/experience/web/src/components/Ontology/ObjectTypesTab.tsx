import { useState } from "react";
import {
  Button,
  Callout,
  Card,
  Checkbox,
  Dialog,
  DialogBody,
  DialogFooter,
  FormGroup,
  HTMLSelect,
  InputGroup,
  Tag,
} from "@blueprintjs/core";
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
  useValueTypes,
  useSharedPropertyTypes,
  useCreateSharedPropertyType,
  useRelationTypes,
} from "../../api/hooks";
import type { ObjectType } from "../../api/knowledge";
import { ApiError } from "../../api/client";
import { CardGrid, EmptyState, ErrorCallout } from "../common/ListPrimitives";
import { ResourceActionsMenu, ResourceTagBadges } from "../common/ResourceActionsMenu";
import { BranchesDialog } from "./BranchesDialog";
import { OntologyTabHeader } from "./OntologyTabLayout";
import { DerivedPropertiesEditor } from "./DerivedPropertiesEditor";
import {
  buildEditableDerived,
  type EditableDerivedProperty,
  serializeDerivedProperties,
} from "./derivedEditorUtils";
import { ObjectTypePropertyEditor } from "./PropertyEditor";
import {
  buildEditableProperties,
  type EditableProperty,
  serializePropertyEditor,
  suggestSharedApiName,
} from "./propertyEditorUtils";

function ManageObjectTypeDialog({ objectType, onClose }: { objectType: ObjectType; onClose: () => void }) {
  const monacoTheme = useMonacoEditorTheme();
  const { data: versions = [] } = useObjectTypeVersions(objectType.name);
  const { data: interfaces = [] } = useInterfaces();
  const { data: markings = [] } = useMarkings();
  const { data: projects = [] } = useProjects();
  const { data: valueTypes = [] } = useValueTypes();
  const { data: sharedPropertyTypes = [] } = useSharedPropertyTypes();
  const { data: relationTypes = [] } = useRelationTypes();
  const { data: allObjectTypes = [] } = useObjectTypes();
  const createSharedPropertyType = useCreateSharedPropertyType();
  const propose = useProposeObjectTypeVersion(objectType.name);
  const publish = usePublishObjectTypeVersion(objectType.name);

  const [description, setDescription] = useState(objectType.description);
  const [projectUrn, setProjectUrn] = useState(objectType.project_urn ?? "");
  const [primaryKey, setPrimaryKey] = useState(objectType.primary_key ?? "id");
  const [titleKey, setTitleKey] = useState(objectType.title_key ?? "");
  const [pluralDisplayName, setPluralDisplayName] = useState(objectType.plural_display_name ?? "");
  const [lifecycleStatus, setLifecycleStatus] = useState(objectType.lifecycle_status ?? "experimental");
  const [visibility, setVisibility] = useState(objectType.visibility ?? "normal");
  const [icon, setIcon] = useState(objectType.icon ?? "");
  const [implementsSet, setImplementsSet] = useState<Set<string>>(new Set(objectType.implements ?? []));
  const [markingsSet, setMarkingsSet] = useState<Set<string>>(new Set(objectType.markings ?? []));
  const [properties, setProperties] = useState<EditableProperty[]>(() =>
    buildEditableProperties(
      objectType.property_mapping ?? {},
      objectType.property_types ?? {},
      objectType.property_formats ?? {},
    ),
  );
  const [selectedProperty, setSelectedProperty] = useState<string | null>(
    () => Object.keys(objectType.property_mapping ?? {})[0] ?? null,
  );
  const [derivedProperties, setDerivedProperties] = useState<EditableDerivedProperty[]>(() =>
    buildEditableDerived(objectType.derived_properties ?? {}),
  );
  const [selectedDerived, setSelectedDerived] = useState<string | null>(
    () => Object.keys(objectType.derived_properties ?? {})[0] ?? null,
  );
  const [conditionalFormatsJson, setConditionalFormatsJson] = useState(
    JSON.stringify(objectType.conditional_formats ?? {}, null, 2),
  );
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [publishingVersion, setPublishingVersion] = useState<number | null>(null);

  const propertyNames = properties.map((p) => p.name).filter(Boolean);

  function toggle(set: Set<string>, setSet: (s: Set<string>) => void, value: string) {
    const next = new Set(set);
    if (next.has(value)) next.delete(value);
    else next.add(value);
    setSet(next);
  }

  async function proposeVersion() {
    setError(null);
    setOk(null);
    let conditional_formats;
    try {
      conditional_formats = JSON.parse(conditionalFormatsJson);
    } catch {
      setError("Conditional formats must be valid JSON.");
      return;
    }
    const { property_mapping, property_types, property_formats } = serializePropertyEditor(properties);
    const derived_properties = serializeDerivedProperties(derivedProperties);
    if (Object.keys(property_mapping).length === 0) {
      setError("At least one property with an API name and backing column is required.");
      return;
    }
    if (!property_mapping[primaryKey]) {
      setError(`Primary key "${primaryKey}" must be one of the mapped properties.`);
      return;
    }
    if (titleKey && !property_mapping[titleKey]) {
      setError(`Title key "${titleKey}" must be one of the mapped properties.`);
      return;
    }
    try {
      const draft = await propose.mutateAsync({
        description,
        implements: [...implementsSet],
        markings: [...markingsSet],
        property_mapping,
        property_types,
        derived_properties,
        property_formats,
        conditional_formats,
        project_urn: projectUrn || undefined,
        primary_key: primaryKey,
        title_key: titleKey || null,
        plural_display_name: pluralDisplayName,
        lifecycle_status: lifecycleStatus,
        visibility,
        icon: icon || null,
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
    <Dialog isOpen title={`${objectType.name} — versions`} onClose={onClose} style={{ width: 820 }}>
      <DialogBody>
        <div className="hl-mb-md">
          <div className="hl-section-title hl-mb-sm">Version history</div>
          <div className="hl-grid-gap-sm">
            {versions.map((v) => (
              <div key={v.id} className="hl-version-row">
                <span>
                  v{v.version}{" "}
                  <Tag minimal intent={v.status === "published" ? "success" : "none"}>
                    {v.status}
                  </Tag>
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
        <FormGroup label="Primary key">
          <HTMLSelect fill value={primaryKey} onChange={(e) => setPrimaryKey(e.target.value)}>
            {propertyNames.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Title key" helperText="Display name property for instances">
          <HTMLSelect fill value={titleKey} onChange={(e) => setTitleKey(e.target.value)}>
            <option value="">(fallback to primary key)</option>
            {propertyNames.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Plural display name">
          <InputGroup value={pluralDisplayName} onChange={(e) => setPluralDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Lifecycle status">
          <HTMLSelect
            fill
            value={lifecycleStatus}
            onChange={(e) => setLifecycleStatus(e.target.value as typeof lifecycleStatus)}
          >
            <option value="experimental">experimental</option>
            <option value="active">active</option>
            <option value="deprecated">deprecated</option>
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="ObjectType visibility">
          <HTMLSelect fill value={visibility} onChange={(e) => setVisibility(e.target.value as typeof visibility)}>
            <option value="prominent">prominent</option>
            <option value="normal">normal</option>
            <option value="hidden">hidden</option>
          </HTMLSelect>
        </FormGroup>
        <FormGroup label="Icon" helperText="Blueprint icon name">
          <InputGroup value={icon} onChange={(e) => setIcon(e.target.value)} placeholder="people" />
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

        <FormGroup label="Properties" helperText="Edit type, format, visibility, and backing column — then propose.">
          <ObjectTypePropertyEditor
            properties={properties}
            selectedName={selectedProperty}
            onSelect={setSelectedProperty}
            onChange={setProperties}
            primaryKey={primaryKey}
            valueTypes={valueTypes}
            sharedPropertyTypes={sharedPropertyTypes}
            convertPending={createSharedPropertyType.isPending}
            onConvertToShared={async (prop) => {
              const apiName = suggestSharedApiName(prop.name);
              try {
                await createSharedPropertyType.mutateAsync({
                  api_name: apiName,
                  display_name: prop.name,
                  value_type: prop.valueType,
                  description: `Shared from ${objectType.name}.${prop.name}`,
                });
                setProperties((current) =>
                  current.map((p) =>
                    p.name === prop.name
                      ? { ...p, typeKind: "shared_property_type", sharedPropertyType: apiName, valueType: "" }
                      : p,
                  ),
                );
                setOk(`Created shared property "${apiName}" and attached it to ${prop.name}.`);
              } catch (err) {
                setError(err instanceof ApiError ? err.message : "Could not convert to shared property");
              }
            }}
          />
        </FormGroup>

        <FormGroup
          label="Derived properties"
          helperText="Function plugin, multi-hop link aggregate (≤3), or struct/array reducer."
        >
          <DerivedPropertiesEditor
            objectType={objectType}
            properties={derivedProperties}
            selectedName={selectedDerived}
            onSelect={setSelectedDerived}
            onChange={setDerivedProperties}
            relationTypes={relationTypes}
            objectTypes={allObjectTypes}
          />
        </FormGroup>
        <FormGroup
          label="Conditional formats (advanced)"
          helperText={
            <span className="hl-mono">
              {"{"}property: [{"{"}condition, style{"}"}, ...]{"}"}
            </span>
          }
        >
          <div className="hl-json-editor">
            <Editor
              height="90px"
              defaultLanguage="json"
              theme={monacoTheme}
              value={conditionalFormatsJson}
              onChange={(v) => setConditionalFormatsJson(v ?? "")}
              options={{ minimap: { enabled: false }, fontSize: 12 }}
            />
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
              {ot.lifecycle_status && (
                <Tag
                  minimal
                  intent={
                    ot.lifecycle_status === "active" ? "success" : ot.lifecycle_status === "deprecated" ? "warning" : "none"
                  }
                >
                  {ot.lifecycle_status}
                </Tag>
              )}
              {ot.visibility && ot.visibility !== "normal" && <Tag minimal>{ot.visibility}</Tag>}
              {ot.title_key && <Tag minimal>title:{ot.title_key}</Tag>}
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
