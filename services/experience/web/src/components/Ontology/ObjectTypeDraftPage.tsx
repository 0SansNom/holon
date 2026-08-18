import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "@tanstack/react-router";
import {
  Button,
  Callout,
  Checkbox,
  FormGroup,
  HTMLSelect,
  InputGroup,
  Tab,
  Tabs,
  Tag,
  type TabId,
} from "@blueprintjs/core";
import Editor from "@monaco-editor/react";
import { useMonacoEditorTheme } from "../../hooks/useMonacoEditorTheme";
import {
  useCreateSharedPropertyType,
  useInterfaces,
  useMarkings,
  useObjectType,
  useObjectTypes,
  useObjectTypeVersions,
  useObjectTypeGroups,
  useProjects,
  useProposeObjectTypeVersion,
  usePublishObjectTypeVersion,
  useReindexObjectTypeSearch,
  useRelationTypes,
  useSharedPropertyTypes,
  useValueTypes,
  useActions,
  useObjects,
} from "../../api/hooks";
import type { ObjectType } from "../../api/knowledge";
import { ApiError } from "../../api/client";
import { DetailPage, PageSection } from "../common/PageLayout";
import { EmptyState, ErrorCallout } from "../common/ListPrimitives";
import { DerivedPropertiesEditor } from "./DerivedPropertiesEditor";
import { ObjectTypePropertyEditor } from "./PropertyEditor";
import {
  serializePropertyEditor,
  suggestSharedApiName,
  stripStructFieldColumns,
} from "./propertyEditorUtils";
import { OBJECT_TYPE_LIFECYCLE_STATUSES } from "./lifecycleUtils";
import { effectiveInterfaceContract } from "./interfaceContractUtils";
import { ObjectTypeOverview } from "./ObjectTypeOverview";
import { useApplications } from "../../api/hooks/useExperienceHooks";
import { useOntologyDiscoverStore } from "../../store/ontologyDiscover";
import {
  objectTypeDraftFormFromRecord,
  patchInterfacePropertyBinding,
  patchNestedBinding,
  prepareObjectTypePropose,
  relationTypesTouchingObjectType,
  toggleSetValue,
  type ObjectTypeDraftForm,
  type ObjectTypeDraftStep,
} from "./objectTypeDraft";

function ObjectTypeDraftEditor({ objectType }: { objectType: ObjectType }) {
  const monacoTheme = useMonacoEditorTheme();
  const { data: versions = [] } = useObjectTypeVersions(objectType.name);
  const { data: interfaces = [] } = useInterfaces();
  const interfacesByName = new Map(interfaces.map((iface) => [iface.name, iface]));
  const { data: markings = [] } = useMarkings();
  const { data: projects = [] } = useProjects();
  const { data: valueTypes = [] } = useValueTypes();
  const { data: sharedPropertyTypes = [] } = useSharedPropertyTypes();
  const { data: relationTypes = [] } = useRelationTypes();
  const { data: allObjectTypes = [] } = useObjectTypes();
  const { data: actions = [] } = useActions();
  const { data: groups = [] } = useObjectTypeGroups();
  const { data: applications = [] } = useApplications();
  const { data: sampleRows = [] } = useObjects(objectType.name);
  const createSharedPropertyType = useCreateSharedPropertyType();
  const propose = useProposeObjectTypeVersion(objectType.name);
  const publish = usePublishObjectTypeVersion(objectType.name);
  const reindexSearch = useReindexObjectTypeSearch(objectType.name);
  const recordVisit = useOntologyDiscoverStore((s) => s.recordVisit);
  const isFavorite = useOntologyDiscoverStore((s) => s.isFavorite("object_type", objectType.name));
  const toggleFavorite = useOntologyDiscoverStore((s) => s.toggleFavorite);

  useEffect(() => {
    recordVisit("object_type", objectType.name);
  }, [objectType.name, recordVisit]);

  const groupsContaining = useMemo(
    () => groups.filter((g) => g.object_types.includes(objectType.name)).map((g) => g.name),
    [groups, objectType.name],
  );
  const applicationsUsing = useMemo(
    () =>
      applications
        .filter((app) => (app.dependencies?.objectTypes ?? []).includes(objectType.name))
        .map((app) => ({ name: app.name })),
    [applications, objectType.name],
  );

  const [step, setStep] = useState<ObjectTypeDraftStep>("overview");
  const [form, setForm] = useState<ObjectTypeDraftForm>(() => objectTypeDraftFormFromRecord(objectType));
  const [selectedProperty, setSelectedProperty] = useState<string | null>(
    () => Object.keys(objectType.property_mapping ?? {})[0] ?? null,
  );
  const [selectedDerived, setSelectedDerived] = useState<string | null>(
    () => Object.keys(objectType.derived_properties ?? {})[0] ?? null,
  );
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [publishingVersion, setPublishingVersion] = useState<number | null>(null);

  function patchForm(patch: Partial<ObjectTypeDraftForm>) {
    setForm((prev) => ({ ...prev, ...patch }));
  }

  const propertyNames = form.properties.map((p) => p.name).filter(Boolean);
  const draftCount = versions.filter((v) => v.status === "draft").length;

  async function proposeVersion() {
    setError(null);
    setOk(null);
    const prepared = prepareObjectTypePropose(form);
    if (!prepared.ok) {
      setError(prepared.error);
      setStep(prepared.step);
      return;
    }
    try {
      const draft = await propose.mutateAsync(prepared.body);
      setOk(`Proposed version ${draft.version} (draft) — publish it from the Versions step when ready.`);
      setStep("versions");
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
    <DetailPage
      breadcrumbs={[
        { label: "Ontology", to: "/ontology" },
        { label: objectType.name },
      ]}
      title={objectType.name}
      description={
        <>
          Edit a draft schema, then propose a version. Live schema is v{objectType.version}
          {objectType.lifecycle_status ? ` · ${objectType.lifecycle_status}` : ""}.
        </>
      }
      actions={
        <div className="hl-flex-row hl-gap-sm" style={{ flexWrap: "wrap" }}>
          <Button
            icon={isFavorite ? "star" : "star-empty"}
            intent={isFavorite ? "warning" : "none"}
            onClick={() => toggleFavorite("object_type", objectType.name)}
          >
            {isFavorite ? "Favorited" : "Favorite"}
          </Button>
          <Link to="/objects/$type" params={{ type: objectType.name }}>
            <Button icon="th">Browse objects</Button>
          </Link>
          <Link to="/lineage/$urn" params={{ urn: objectType.urn }}>
            <Button icon="diagram-tree">Lineage</Button>
          </Link>
          <Button
            intent="primary"
            icon="git-commit"
            loading={propose.isPending}
            onClick={() => void proposeVersion()}
          >
            Propose version
          </Button>
        </div>
      }
    >
      {(error || ok) && (
        <div className="hl-mb-md">
          {error && <ErrorCallout>{error}</ErrorCallout>}
          {ok && (
            <Callout intent="success" className={error ? "hl-mt-sm" : undefined}>
              {ok}
            </Callout>
          )}
        </div>
      )}

      <Tabs
        id="object-type-draft-steps"
        selectedTabId={step}
        onChange={(id: TabId) => setStep(id as ObjectTypeDraftStep)}
        renderActiveTabPanelOnly
        className="hl-ot-draft-tabs"
      >
        <Tab
          id="overview"
          title="Overview"
          panel={
            <ObjectTypeOverview
              objectType={objectType}
              properties={form.properties}
              derivedCount={form.derivedProperties.length}
              relationTypes={relationTypes}
              actions={actions}
              groupsContaining={groupsContaining}
              applicationsUsing={applicationsUsing}
              onNavigateStep={(next) => setStep(next)}
            />
          }
        />
        <Tab
          id="identity"
          title="Identity"
          panel={
            <PageSection title="Identity & governance">
              <div className="hl-ot-draft-grid">
                <FormGroup label="Description">
                  <InputGroup value={form.description} onChange={(e) => patchForm({ description: e.target.value })} />
                </FormGroup>
                <FormGroup label="Plural display name">
                  <InputGroup
                    value={form.pluralDisplayName}
                    onChange={(e) => patchForm({ pluralDisplayName: e.target.value })}
                  />
                </FormGroup>
                <FormGroup label="Primary key">
                  <HTMLSelect fill value={form.primaryKey} onChange={(e) => patchForm({ primaryKey: e.target.value })}>
                    {propertyNames.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Title key" helperText="Display name property for instances">
                  <HTMLSelect fill value={form.titleKey} onChange={(e) => patchForm({ titleKey: e.target.value })}>
                    <option value="">(fallback to primary key)</option>
                    {propertyNames.map((p) => (
                      <option key={p} value={p}>
                        {p}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Lifecycle status">
                  <HTMLSelect
                    fill
                    value={form.lifecycleStatus}
                    onChange={(e) => patchForm({ lifecycleStatus: e.target.value })}
                  >
                    {OBJECT_TYPE_LIFECYCLE_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                {form.lifecycleStatus === "deprecated" && (
                  <>
                    <FormGroup label="Deprecation reason" helperText="Required — why this ObjectType is being retired">
                      <InputGroup
                        value={form.deprecationReason}
                        onChange={(e) => patchForm({ deprecationReason: e.target.value })}
                        placeholder="Replaced by OrderV2"
                      />
                    </FormGroup>
                    <FormGroup label="Deprecation deadline" helperText="Expected removal date (YYYY-MM-DD)">
                      <InputGroup
                        type="date"
                        value={form.deprecationDeadline}
                        onChange={(e) => patchForm({ deprecationDeadline: e.target.value })}
                      />
                    </FormGroup>
                    <FormGroup label="Replacement URN" helperText="Optional — resource that replaces this one">
                      <InputGroup
                        className="hl-mono"
                        value={form.replacementUrn}
                        onChange={(e) => patchForm({ replacementUrn: e.target.value })}
                        placeholder="hl:…:object-type:OrderV2"
                      />
                    </FormGroup>
                  </>
                )}
                <FormGroup label="ObjectType visibility">
                  <HTMLSelect
                    fill
                    value={form.visibility}
                    onChange={(e) => patchForm({ visibility: e.target.value })}
                  >
                    <option value="prominent">prominent</option>
                    <option value="normal">normal</option>
                    <option value="hidden">hidden</option>
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Icon" helperText="Blueprint icon name">
                  <InputGroup
                    value={form.icon}
                    onChange={(e) => patchForm({ icon: e.target.value })}
                    placeholder="people"
                  />
                </FormGroup>
                <FormGroup
                  label="Project"
                  helperText="Narrows access to this project's members, additive on top of workspace access"
                >
                  <HTMLSelect fill value={form.projectUrn} onChange={(e) => patchForm({ projectUrn: e.target.value })}>
                    <option value="">No project</option>
                    {projects.map((p) => (
                      <option key={p.urn} value={p.urn}>
                        {p.name}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
              </div>

              {interfaces.length > 0 && (
                <FormGroup label="Implements" className="hl-mt-md">
                  <div className="hl-ot-draft-check-grid">
                    {interfaces.map((iface) => (
                      <Checkbox
                        key={iface.name}
                        label={
                          (iface.parent_interfaces?.length ?? 0) > 0
                            ? `${iface.name} (extends ${iface.parent_interfaces!.join(", ")})`
                            : iface.name
                        }
                        checked={form.implements.has(iface.name)}
                        onChange={() =>
                          setForm((prev) => ({
                            ...prev,
                            implements: toggleSetValue(prev.implements, iface.name),
                          }))
                        }
                      />
                    ))}
                  </div>
                </FormGroup>
              )}

              {[...form.implements].some((name) => {
                const iface = interfacesByName.get(name);
                if (!iface) return false;
                return effectiveInterfaceContract(iface, interfacesByName).link_constraints.length > 0;
              }) && (
                <FormGroup label="Link constraint bindings" className="hl-mt-md">
                  <p className="hl-text-muted-sm hl-mb-sm">
                    Map each interface link constraint (including inherited) to a RelationType that touches this
                    ObjectType.
                  </p>
                  {[...form.implements].map((ifaceName) => {
                    const iface = interfacesByName.get(ifaceName);
                    if (!iface) return null;
                    const constraints = effectiveInterfaceContract(iface, interfacesByName).link_constraints;
                    if (constraints.length === 0) return null;
                    const touching = relationTypesTouchingObjectType(relationTypes, objectType.name);
                    return (
                      <div key={ifaceName} className="hl-mt-xs">
                        <div className="hl-text-muted-sm">{ifaceName}</div>
                        {constraints.map((c) => (
                          <FormGroup
                            key={c.api_name}
                            label={`${c.api_name} → ${c.target} (${c.cardinality}${c.required ? "" : ", optional"})`}
                          >
                            <HTMLSelect
                              fill
                              value={form.linkConstraintBindings[ifaceName]?.[c.api_name] ?? ""}
                              onChange={(e) => {
                                const value = e.target.value;
                                setForm((prev) => ({
                                  ...prev,
                                  linkConstraintBindings: patchNestedBinding(
                                    prev.linkConstraintBindings,
                                    ifaceName,
                                    c.api_name,
                                    value || undefined,
                                  ),
                                }));
                              }}
                            >
                              <option value="">{c.required ? "Select RelationType…" : "None (optional)"}</option>
                              {touching.map((rt) => (
                                <option key={rt.name} value={rt.name}>
                                  {rt.name} ({rt.cardinality})
                                </option>
                              ))}
                            </HTMLSelect>
                          </FormGroup>
                        ))}
                      </div>
                    );
                  })}
                </FormGroup>
              )}

              {[...form.implements].some((name) => {
                const iface = interfacesByName.get(name);
                if (!iface) return false;
                return effectiveInterfaceContract(iface, interfacesByName).required_properties.length > 0;
              }) && (
                <FormGroup label="Interface property bindings" className="hl-mt-md">
                  <p className="hl-text-muted-sm hl-mb-sm">
                    Optional path when a required interface property is satisfied by a struct field (e.g.{" "}
                    <code>address.city</code>). Leave blank to use the same top-level property name.
                  </p>
                  {[...form.implements].map((ifaceName) => {
                    const iface = interfacesByName.get(ifaceName);
                    if (!iface) return null;
                    const props = effectiveInterfaceContract(iface, interfacesByName).required_properties;
                    if (props.length === 0) return null;
                    return (
                      <div key={`prop-bind-${ifaceName}`} className="hl-mt-xs">
                        <div className="hl-text-muted-sm">{ifaceName}</div>
                        {props.map((propName) => (
                          <FormGroup key={propName} label={propName}>
                            <InputGroup
                              fill
                              className="hl-mono"
                              placeholder={propName}
                              value={form.interfacePropertyBindings[ifaceName]?.[propName] ?? ""}
                              onChange={(e) => {
                                setForm((prev) => ({
                                  ...prev,
                                  interfacePropertyBindings: patchInterfacePropertyBinding(
                                    prev.interfacePropertyBindings,
                                    ifaceName,
                                    propName,
                                    e.target.value,
                                  ),
                                }));
                              }}
                            />
                          </FormGroup>
                        ))}
                      </div>
                    );
                  })}
                </FormGroup>
              )}

              {markings.length > 0 && (
                <FormGroup label="Markings" className="hl-mt-md">
                  <div className="hl-ot-draft-check-grid">
                    {markings.map((m) => (
                      <Checkbox
                        key={m.name}
                        label={
                          m.category_name && m.category_name !== "Default"
                            ? `${m.name} (${m.category_name})`
                            : m.name
                        }
                        checked={form.markings.has(m.name)}
                        onChange={() =>
                          setForm((prev) => ({
                            ...prev,
                            markings: toggleSetValue(prev.markings, m.name),
                          }))
                        }
                      />
                    ))}
                  </div>
                </FormGroup>
              )}
            </PageSection>
          }
        />

        <Tab
          id="properties"
          title={`Properties (${form.properties.length})`}
          panel={
            <PageSection title="Properties">
              <p className="hl-text-muted-sm hl-mb-md">
                Edit type, format, visibility, and backing column — then propose a version when ready.
              </p>
              <ObjectTypePropertyEditor
                properties={form.properties}
                selectedName={selectedProperty}
                onSelect={setSelectedProperty}
                onChange={(properties) => patchForm({ properties })}
                primaryKey={form.primaryKey}
                valueTypes={valueTypes}
                sharedPropertyTypes={sharedPropertyTypes}
                sampleRows={sampleRows as Array<Record<string, unknown>>}
                convertPending={createSharedPropertyType.isPending}
                onConvertToShared={async (prop) => {
                  const apiName = suggestSharedApiName(prop.name);
                  try {
                    if (prop.typeKind === "struct") {
                      const { property_types } = serializePropertyEditor([prop]);
                      const rule = property_types[prop.name];
                      if (!rule || rule.kind !== "struct") {
                        throw new Error("Struct fields are incomplete — add at least one typed field.");
                      }
                      await createSharedPropertyType.mutateAsync({
                        api_name: apiName,
                        display_name: prop.name,
                        struct_properties: stripStructFieldColumns(rule.properties),
                        description: `Shared struct from ${objectType.name}.${prop.name}`,
                      });
                    } else {
                      await createSharedPropertyType.mutateAsync({
                        api_name: apiName,
                        display_name: prop.name,
                        value_type: prop.valueType,
                        description: `Shared from ${objectType.name}.${prop.name}`,
                      });
                    }
                    setForm((current) => ({
                      ...current,
                      properties: current.properties.map((p) =>
                        p.name === prop.name
                          ? {
                              ...p,
                              typeKind: "shared_property_type",
                              sharedPropertyType: apiName,
                              valueType: "",
                            }
                          : p,
                      ),
                    }));
                    setOk(`Created shared property "${apiName}" and attached it to ${prop.name}.`);
                  } catch (err) {
                    setError(err instanceof ApiError ? err.message : "Could not convert to shared property");
                  }
                }}
              />
            </PageSection>
          }
        />

        <Tab
          id="derived"
          title={`Derived (${form.derivedProperties.length})`}
          panel={
            <PageSection title="Derived properties">
              <p className="hl-text-muted-sm hl-mb-md">
                Function plugin, multi-hop link aggregate (≤3), or struct/array reducer.
              </p>
              <DerivedPropertiesEditor
                objectType={objectType}
                properties={form.derivedProperties}
                selectedName={selectedDerived}
                onSelect={setSelectedDerived}
                onChange={(derivedProperties) => patchForm({ derivedProperties })}
                relationTypes={relationTypes}
                objectTypes={allObjectTypes}
              />
            </PageSection>
          }
        />

        <Tab
          id="datasources"
          title="Datasources"
          panel={
            <PageSection title="Datasources">
              <p className="hl-text-muted-sm hl-mb-md">
                Backing dataset and property → column mapping for this ObjectType (Foundry Datasources view).
                Edit mappings on the Properties tab, then propose a version.
              </p>
              <dl className="hl-ot-overview-meta hl-mb-md">
                <div>
                  <dt>Source dataset</dt>
                  <dd className="hl-mono" title={objectType.source_dataset_urn}>
                    {objectType.source_dataset_urn || "—"}
                  </dd>
                </div>
                <div>
                  <dt>Primary key</dt>
                  <dd className="hl-mono">{form.primaryKey || "—"}</dd>
                </div>
                <div>
                  <dt>Title key</dt>
                  <dd className="hl-mono">{form.titleKey || form.primaryKey || "—"}</dd>
                </div>
                <div>
                  <dt>Mapped properties</dt>
                  <dd>{form.properties.filter((p) => p.name && p.column).length}</dd>
                </div>
              </dl>
              <div className="hl-flex-row hl-gap-sm hl-mb-md" style={{ flexWrap: "wrap" }}>
                <Link to="/lineage/$urn" params={{ urn: objectType.urn }}>
                  <Button small icon="diagram-tree">
                    Open lineage
                  </Button>
                </Link>
                <Link to="/catalog">
                  <Button small icon="th-list">
                    Open Catalog
                  </Button>
                </Link>
                <Button small icon="edit" onClick={() => setStep("properties")}>
                  Edit property mapping
                </Button>
                <Button small icon="id-number" onClick={() => setStep("identity")}>
                  Edit identity keys
                </Button>
              </div>
              {form.properties.filter((p) => p.name && p.column).length === 0 ? (
                <EmptyState>No property → column mappings yet.</EmptyState>
              ) : (
                <table className="hl-data-table hl-data-table-compact">
                  <thead>
                    <tr>
                      <th>Property</th>
                      <th>Backing column</th>
                      <th>Type</th>
                      <th>Role</th>
                    </tr>
                  </thead>
                  <tbody>
                    {form.properties
                      .filter((p) => p.name && p.column)
                      .map((p) => (
                        <tr key={p.name}>
                          <td className="hl-mono">{p.name}</td>
                          <td className="hl-mono">{p.column}</td>
                          <td className="hl-mono">
                            {p.typeKind === "shared_property_type"
                              ? p.sharedPropertyType || "SPT"
                              : p.typeKind === "struct"
                                ? "struct"
                                : p.typeKind === "array"
                                  ? "array"
                                  : p.valueType || "—"}
                          </td>
                          <td>
                            {p.name === form.primaryKey
                              ? "primary key"
                              : p.name === form.titleKey
                                ? "title key"
                                : "—"}
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
              )}
            </PageSection>
          }
        />

        <Tab
          id="advanced"
          title="Advanced"
          panel={
            <PageSection title="Conditional formats">
              <FormGroup
                helperText={
                  <span className="hl-mono">
                    {"{"}property: [{"{"}condition, style{"}"}, ...]{"}"}
                  </span>
                }
              >
                <div className="hl-json-editor hl-ot-draft-json">
                  <Editor
                    height="220px"
                    defaultLanguage="json"
                    theme={monacoTheme}
                    value={form.conditionalFormatsJson}
                    onChange={(v) => patchForm({ conditionalFormatsJson: v ?? "" })}
                    options={{ minimap: { enabled: false }, fontSize: 12 }}
                  />
                </div>
              </FormGroup>
              <FormGroup
                label="Search index"
                helperText="Rebuild OpenSearch from the serving store after changing render hints (Foundry Reindex datasources)."
              >
                <Button
                  icon="refresh"
                  loading={reindexSearch.isPending}
                  onClick={() => {
                    setError(null);
                    setOk(null);
                    void reindexSearch
                      .mutateAsync()
                      .then((result) =>
                        setOk(
                          `Reindexed ${result.indexed} row(s)` +
                            (result.skipped_invalid ? ` (${result.skipped_invalid} skipped — Value Type validation)` : ""),
                        ),
                      )
                      .catch((err) => setError(err instanceof Error ? err.message : "Reindex failed"));
                  }}
                >
                  Reindex search
                </Button>
              </FormGroup>
            </PageSection>
          }
        />

        <Tab
          id="versions"
          title={draftCount > 0 ? `Versions (${draftCount} draft)` : "Versions"}
          panel={
            <PageSection title="Version history">
              <p className="hl-text-muted-sm hl-mb-md">
                Propose creates a draft. Publish promotes a draft to the live ObjectType schema.
              </p>
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
                      <Button
                        small
                        intent="primary"
                        loading={publishingVersion === v.version}
                        onClick={() => void publishVersion(v.version)}
                      >
                        Publish
                      </Button>
                    )}
                  </div>
                ))}
                {versions.length === 0 && (
                  <p className="hl-text-muted">No versions proposed yet — edit the schema and click Propose version.</p>
                )}
              </div>
            </PageSection>
          }
        />
      </Tabs>

      <div className="hl-ot-draft-footer">
        <span className="hl-text-muted-sm">
          Changes are local until you propose.{" "}
          <Link to="/ontology" className="hl-link-accent">
            Back to Ontology
          </Link>
        </span>
        <Button intent="primary" icon="git-commit" loading={propose.isPending} onClick={() => void proposeVersion()}>
          Propose version
        </Button>
      </div>
    </DetailPage>
  );
}

export function ObjectTypeDraftPage() {
  const { name } = useParams({ from: "/shell/ontology/object-types/$name" });
  const { data: objectType } = useObjectType(name);

  if (!objectType) {
    return (
      <DetailPage
        breadcrumbs={[{ label: "Ontology", to: "/ontology" }, { label: name }]}
        title={name}
      >
        <EmptyState>ObjectType not found.</EmptyState>
      </DetailPage>
    );
  }

  return <ObjectTypeDraftEditor key={objectType.urn} objectType={objectType} />;
}
