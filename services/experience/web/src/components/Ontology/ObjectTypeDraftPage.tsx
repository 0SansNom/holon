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
  stripStructFieldColumns,
} from "./propertyEditorUtils";
import { OBJECT_TYPE_LIFECYCLE_STATUSES } from "./lifecycleUtils";
import { effectiveInterfaceContract } from "./interfaceContractUtils";
import { ObjectTypeOverview } from "./ObjectTypeOverview";
import { useApplications } from "../../api/hooks/useExperienceHooks";
import { useOntologyDiscoverStore } from "../../store/ontologyDiscover";

type DraftStep = "overview" | "identity" | "properties" | "derived" | "datasources" | "advanced" | "versions";

function toggle(set: Set<string>, setSet: (s: Set<string>) => void, value: string) {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  setSet(next);
}

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

  const [step, setStep] = useState<DraftStep>("overview");
  const [description, setDescription] = useState(objectType.description);
  const [projectUrn, setProjectUrn] = useState(objectType.project_urn ?? "");
  const [primaryKey, setPrimaryKey] = useState(objectType.primary_key ?? "id");
  const [titleKey, setTitleKey] = useState(objectType.title_key ?? "");
  const [pluralDisplayName, setPluralDisplayName] = useState(objectType.plural_display_name ?? "");
  const [lifecycleStatus, setLifecycleStatus] = useState(objectType.lifecycle_status ?? "experimental");
  const [deprecationReason, setDeprecationReason] = useState(objectType.deprecation_reason ?? "");
  const [deprecationDeadline, setDeprecationDeadline] = useState(
    (objectType.deprecation_deadline ?? "").toString().slice(0, 10),
  );
  const [replacementUrn, setReplacementUrn] = useState(objectType.replacement_urn ?? "");
  const [visibility, setVisibility] = useState(objectType.visibility ?? "normal");
  const [icon, setIcon] = useState(objectType.icon ?? "");
  const [implementsSet, setImplementsSet] = useState<Set<string>>(new Set(objectType.implements ?? []));
  const [linkConstraintBindings, setLinkConstraintBindings] = useState<Record<string, Record<string, string>>>(
    () => objectType.link_constraint_bindings ?? {},
  );
  const [interfacePropertyBindings, setInterfacePropertyBindings] = useState<
    Record<string, Record<string, string>>
  >(() => objectType.interface_property_bindings ?? {});
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
  const draftCount = versions.filter((v) => v.status === "draft").length;

  async function proposeVersion() {
    setError(null);
    setOk(null);
    let conditional_formats;
    try {
      conditional_formats = JSON.parse(conditionalFormatsJson);
    } catch {
      setError("Conditional formats must be valid JSON.");
      setStep("advanced");
      return;
    }
    const { property_mapping, property_types, property_formats } = serializePropertyEditor(properties);
    const derived_properties = serializeDerivedProperties(derivedProperties);
    if (Object.keys(property_mapping).length === 0) {
      setError("At least one property with an API name and backing column is required.");
      setStep("properties");
      return;
    }
    if (!property_mapping[primaryKey]) {
      setError(`Primary key "${primaryKey}" must be one of the mapped properties.`);
      setStep("identity");
      return;
    }
    if (titleKey && !property_mapping[titleKey]) {
      setError(`Title key "${titleKey}" must be one of the mapped properties.`);
      setStep("identity");
      return;
    }
    try {
      const draft = await propose.mutateAsync({
        description,
        implements: [...implementsSet],
        link_constraint_bindings: linkConstraintBindings,
        interface_property_bindings: interfacePropertyBindings,
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
        deprecation_reason: lifecycleStatus === "deprecated" ? deprecationReason : null,
        deprecation_deadline: lifecycleStatus === "deprecated" ? deprecationDeadline || null : null,
        replacement_urn: lifecycleStatus === "deprecated" ? replacementUrn || null : null,
        visibility,
        icon: icon || null,
      });
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
        onChange={(id: TabId) => setStep(id as DraftStep)}
        renderActiveTabPanelOnly
        className="hl-ot-draft-tabs"
      >
        <Tab
          id="overview"
          title="Overview"
          panel={
            <ObjectTypeOverview
              objectType={objectType}
              properties={properties}
              derivedCount={derivedProperties.length}
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
                  <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} />
                </FormGroup>
                <FormGroup label="Plural display name">
                  <InputGroup value={pluralDisplayName} onChange={(e) => setPluralDisplayName(e.target.value)} />
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
                <FormGroup label="Lifecycle status">
                  <HTMLSelect
                    fill
                    value={lifecycleStatus}
                    onChange={(e) =>
                      setLifecycleStatus(e.target.value as "experimental" | "active" | "deprecated")
                    }
                  >
                    {OBJECT_TYPE_LIFECYCLE_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                {lifecycleStatus === "deprecated" && (
                  <>
                    <FormGroup label="Deprecation reason" helperText="Required — why this ObjectType is being retired">
                      <InputGroup
                        value={deprecationReason}
                        onChange={(e) => setDeprecationReason(e.target.value)}
                        placeholder="Replaced by OrderV2"
                      />
                    </FormGroup>
                    <FormGroup label="Deprecation deadline" helperText="Expected removal date (YYYY-MM-DD)">
                      <InputGroup
                        type="date"
                        value={deprecationDeadline}
                        onChange={(e) => setDeprecationDeadline(e.target.value)}
                      />
                    </FormGroup>
                    <FormGroup label="Replacement URN" helperText="Optional — resource that replaces this one">
                      <InputGroup
                        className="hl-mono"
                        value={replacementUrn}
                        onChange={(e) => setReplacementUrn(e.target.value)}
                        placeholder="hl:…:object-type:OrderV2"
                      />
                    </FormGroup>
                  </>
                )}
                <FormGroup label="ObjectType visibility">
                  <HTMLSelect
                    fill
                    value={visibility}
                    onChange={(e) => setVisibility(e.target.value as typeof visibility)}
                  >
                    <option value="prominent">prominent</option>
                    <option value="normal">normal</option>
                    <option value="hidden">hidden</option>
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Icon" helperText="Blueprint icon name">
                  <InputGroup value={icon} onChange={(e) => setIcon(e.target.value)} placeholder="people" />
                </FormGroup>
                <FormGroup
                  label="Project"
                  helperText="Narrows access to this project's members, additive on top of workspace access"
                >
                  <HTMLSelect fill value={projectUrn} onChange={(e) => setProjectUrn(e.target.value)}>
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
                        checked={implementsSet.has(iface.name)}
                        onChange={() => toggle(implementsSet, setImplementsSet, iface.name)}
                      />
                    ))}
                  </div>
                </FormGroup>
              )}

              {[...implementsSet].some((name) => {
                const iface = interfacesByName.get(name);
                if (!iface) return false;
                return effectiveInterfaceContract(iface, interfacesByName).link_constraints.length > 0;
              }) && (
                <FormGroup label="Link constraint bindings" className="hl-mt-md">
                  <p className="hl-text-muted-sm hl-mb-sm">
                    Map each interface link constraint (including inherited) to a RelationType that touches this
                    ObjectType.
                  </p>
                  {[...implementsSet].map((ifaceName) => {
                    const iface = interfacesByName.get(ifaceName);
                    if (!iface) return null;
                    const constraints = effectiveInterfaceContract(iface, interfacesByName).link_constraints;
                    if (constraints.length === 0) return null;
                    const touching = relationTypes.filter((rt) => {
                      const source = rt.source_object_type_urn.split(":").at(-1);
                      const target = rt.target_object_type_urn.split(":").at(-1);
                      return source === objectType.name || target === objectType.name;
                    });
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
                              value={linkConstraintBindings[ifaceName]?.[c.api_name] ?? ""}
                              onChange={(e) => {
                                const value = e.target.value;
                                setLinkConstraintBindings((prev) => {
                                  const nextIface = { ...(prev[ifaceName] ?? {}) };
                                  if (!value) delete nextIface[c.api_name];
                                  else nextIface[c.api_name] = value;
                                  const next = { ...prev };
                                  if (Object.keys(nextIface).length === 0) delete next[ifaceName];
                                  else next[ifaceName] = nextIface;
                                  return next;
                                });
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

              {[...implementsSet].some((name) => {
                const iface = interfacesByName.get(name);
                if (!iface) return false;
                return effectiveInterfaceContract(iface, interfacesByName).required_properties.length > 0;
              }) && (
                <FormGroup label="Interface property bindings" className="hl-mt-md">
                  <p className="hl-text-muted-sm hl-mb-sm">
                    Optional path when a required interface property is satisfied by a struct field (e.g.{" "}
                    <code>address.city</code>). Leave blank to use the same top-level property name.
                  </p>
                  {[...implementsSet].map((ifaceName) => {
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
                              value={interfacePropertyBindings[ifaceName]?.[propName] ?? ""}
                              onChange={(e) => {
                                const value = e.target.value.trim();
                                setInterfacePropertyBindings((prev) => {
                                  const nextIface = { ...(prev[ifaceName] ?? {}) };
                                  if (!value || value === propName) delete nextIface[propName];
                                  else nextIface[propName] = value;
                                  const next = { ...prev };
                                  if (Object.keys(nextIface).length === 0) delete next[ifaceName];
                                  else next[ifaceName] = nextIface;
                                  return next;
                                });
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
                        label={m.name}
                        checked={markingsSet.has(m.name)}
                        onChange={() => toggle(markingsSet, setMarkingsSet, m.name)}
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
          title={`Properties (${properties.length})`}
          panel={
            <PageSection title="Properties">
              <p className="hl-text-muted-sm hl-mb-md">
                Edit type, format, visibility, and backing column — then propose a version when ready.
              </p>
              <ObjectTypePropertyEditor
                properties={properties}
                selectedName={selectedProperty}
                onSelect={setSelectedProperty}
                onChange={setProperties}
                primaryKey={primaryKey}
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
                    setProperties((current) =>
                      current.map((p) =>
                        p.name === prop.name
                          ? {
                              ...p,
                              typeKind: "shared_property_type",
                              sharedPropertyType: apiName,
                              valueType: "",
                            }
                          : p,
                      ),
                    );
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
          title={`Derived (${derivedProperties.length})`}
          panel={
            <PageSection title="Derived properties">
              <p className="hl-text-muted-sm hl-mb-md">
                Function plugin, multi-hop link aggregate (≤3), or struct/array reducer.
              </p>
              <DerivedPropertiesEditor
                objectType={objectType}
                properties={derivedProperties}
                selectedName={selectedDerived}
                onSelect={setSelectedDerived}
                onChange={setDerivedProperties}
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
                  <dd className="hl-mono">{primaryKey || "—"}</dd>
                </div>
                <div>
                  <dt>Title key</dt>
                  <dd className="hl-mono">{titleKey || primaryKey || "—"}</dd>
                </div>
                <div>
                  <dt>Mapped properties</dt>
                  <dd>{properties.filter((p) => p.name && p.column).length}</dd>
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
              {properties.filter((p) => p.name && p.column).length === 0 ? (
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
                    {properties
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
                            {p.name === primaryKey
                              ? "primary key"
                              : p.name === titleKey
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
                    value={conditionalFormatsJson}
                    onChange={(v) => setConditionalFormatsJson(v ?? "")}
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
