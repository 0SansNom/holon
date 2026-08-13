import { useEffect, useMemo, useState } from "react";
import { Button, Callout, Checkbox, FormGroup, HTMLSelect, InputGroup, Tab, Tabs, Tag } from "@blueprintjs/core";
import { Link } from "@tanstack/react-router";
import {
  useSharedPropertyTypes,
  useCreateSharedPropertyType,
  useUpdateSharedPropertyType,
  useDeleteSharedPropertyType,
  useSharedPropertyTypeUsage,
  useSharedPropertyTypePermissions,
  useValueTypes,
  useProjects,
} from "../../api/hooks";
import type { PropertyFormatRule, PropertyRenderHint, SharedPropertyType } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { showSuccess } from "../../lib/toast";
import { BranchesDialog } from "./BranchesDialog";
import { isEphemeralTestName } from "./ephemeralResources";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";
import {
  ALL_RENDER_HINTS,
  type EditableStructField,
  emptyStructFieldExport,
  serializePropertyEditor,
  emptyProperty,
  parseAliasesInput,
  parseTypeClassesInput,
} from "./propertyEditorUtils";

const FORMAT_KINDS = ["", "currency", "numeric", "datetime", "principal", "resource-link", "badge"] as const;

type MetaState = {
  visibility: "prominent" | "normal" | "hidden";
  renderHints: PropertyRenderHint[];
  typeClasses: string;
  aliases: string;
  formatKind: (typeof FORMAT_KINDS)[number];
  formatCurrency: string;
  formatDatetimeStyle: Extract<PropertyFormatRule, { kind: "datetime" }>["style"];
};

const DEFAULT_META: MetaState = {
  visibility: "normal",
  renderHints: ["searchable"],
  typeClasses: "",
  aliases: "",
  formatKind: "",
  formatCurrency: "EUR",
  formatDatetimeStyle: "datetime-short",
};

function metaFromSpt(spt: SharedPropertyType): MetaState {
  const format = spt.property_format;
  return {
    visibility: spt.visibility ?? "normal",
    renderHints: spt.render_hints ? [...spt.render_hints] : ["searchable"],
    typeClasses: (spt.type_classes ?? []).join(", "),
    aliases: (spt.aliases ?? []).join(", "),
    formatKind: format?.kind ?? "",
    formatCurrency: format?.kind === "currency" ? format.currency : "EUR",
    formatDatetimeStyle: format?.kind === "datetime" ? format.style : "datetime-short",
  };
}

function serializeFormat(meta: MetaState): PropertyFormatRule | null {
  if (meta.formatKind === "currency") return { kind: "currency", currency: meta.formatCurrency || "EUR" };
  if (meta.formatKind === "numeric") return { kind: "numeric", style: "decimal" };
  if (meta.formatKind === "datetime") return { kind: "datetime", style: meta.formatDatetimeStyle };
  if (meta.formatKind === "principal") return { kind: "principal" };
  if (meta.formatKind === "resource-link") return { kind: "resource-link", resourceType: "object-type" };
  if (meta.formatKind === "badge") return { kind: "badge", colors: {} };
  return null;
}

function metaPayload(meta: MetaState) {
  const format = serializeFormat(meta);
  return {
    visibility: meta.visibility,
    render_hints: meta.renderHints,
    type_classes: parseTypeClassesInput(meta.typeClasses),
    aliases: parseAliasesInput(meta.aliases),
    property_format: format ?? undefined,
  };
}

function VisibilityField({ meta, onChange }: { meta: MetaState; onChange: (next: MetaState) => void }) {
  return (
    <FormGroup label="Visibility">
      <HTMLSelect
        fill
        value={meta.visibility}
        onChange={(e) => onChange({ ...meta, visibility: e.target.value as MetaState["visibility"] })}
      >
        <option value="prominent">prominent</option>
        <option value="normal">normal</option>
        <option value="hidden">hidden</option>
      </HTMLSelect>
    </FormGroup>
  );
}

function FormatFields({ meta, onChange }: { meta: MetaState; onChange: (next: MetaState) => void }) {
  return (
    <>
      <FormGroup label="Value formatting">
        <HTMLSelect
          fill
          value={meta.formatKind}
          onChange={(e) => onChange({ ...meta, formatKind: e.target.value as MetaState["formatKind"] })}
        >
          {FORMAT_KINDS.map((k) => (
            <option key={k || "none"} value={k}>
              {k || "(none)"}
            </option>
          ))}
        </HTMLSelect>
      </FormGroup>
      {meta.formatKind === "currency" && (
        <FormGroup label="Currency">
          <InputGroup
            value={meta.formatCurrency}
            onChange={(e) => onChange({ ...meta, formatCurrency: e.target.value })}
          />
        </FormGroup>
      )}
      {meta.formatKind === "datetime" && (
        <FormGroup label="Datetime style">
          <HTMLSelect
            fill
            value={meta.formatDatetimeStyle}
            onChange={(e) =>
              onChange({
                ...meta,
                formatDatetimeStyle: e.target.value as MetaState["formatDatetimeStyle"],
              })
            }
          >
            <option value="date">date</option>
            <option value="datetime-short">datetime-short</option>
            <option value="datetime-long">datetime-long</option>
            <option value="iso8601">iso8601</option>
            <option value="relative">relative</option>
            <option value="time">time</option>
          </HTMLSelect>
        </FormGroup>
      )}
    </>
  );
}

function InteractionFields({ meta, onChange }: { meta: MetaState; onChange: (next: MetaState) => void }) {
  return (
    <FormGroup label="Render hints" helperText="Inherited by ObjectTypes that attach this shared property">
      <div className="hl-flex-row hl-flex-wrap">
        {ALL_RENDER_HINTS.map((hint) => (
          <Checkbox
            key={hint}
            label={hint}
            checked={meta.renderHints.includes(hint)}
            onChange={() => {
              const has = meta.renderHints.includes(hint);
              onChange({
                ...meta,
                renderHints: has ? meta.renderHints.filter((h) => h !== hint) : [...meta.renderHints, hint],
              });
            }}
          />
        ))}
      </div>
    </FormGroup>
  );
}

function DetailsFields({ meta, onChange }: { meta: MetaState; onChange: (next: MetaState) => void }) {
  return (
    <>
      <FormGroup label="Type classes" helperText="Comma-separated">
        <InputGroup
          className="hl-mono"
          value={meta.typeClasses}
          onChange={(e) => onChange({ ...meta, typeClasses: e.target.value })}
          placeholder="priority"
        />
      </FormGroup>
      <FormGroup label="Aliases" helperText="Alternate search terms (comma-separated)">
        <InputGroup
          value={meta.aliases}
          onChange={(e) => onChange({ ...meta, aliases: e.target.value })}
          placeholder="hire date, onboarding date"
        />
      </FormGroup>
    </>
  );
}

function UsagePanel({ apiName }: { apiName: string }) {
  const { data: usage = [], isLoading, error } = useSharedPropertyTypeUsage(apiName, !!apiName);
  if (isLoading) return <p className="hl-text-muted-sm">Loading usage…</p>;
  if (error) return <Callout intent="danger">Could not load usage.</Callout>;
  if (usage.length === 0) return <p className="hl-text-muted-sm">Not used by any ObjectType yet.</p>;
  return (
    <ul className="hl-spt-usage-list">
      {usage.map((u) => (
        <li key={u.object_type}>
          <Link to="/ontology/object-types/$name" params={{ name: u.object_type }}>
            {u.object_type}
          </Link>
        </li>
      ))}
    </ul>
  );
}

function UrnRow({ urn }: { urn: string }) {
  return (
    <FormGroup label="URN" helperText="Holon RID equivalent — stable across renames of display name">
      <InputGroup
        className="hl-mono"
        readOnly
        value={urn}
        rightElement={
          <Button
            minimal
            icon="clipboard"
            title="Copy URN"
            onClick={() => void navigator.clipboard.writeText(urn)}
          />
        }
      />
    </FormGroup>
  );
}

function PermissionsPanel({ apiName }: { apiName: string }) {
  const { data, isLoading, error } = useSharedPropertyTypePermissions(apiName, !!apiName);
  if (isLoading) return <p className="hl-text-muted-sm">Loading permissions…</p>;
  if (error) return <Callout intent="danger">Could not load permissions.</Callout>;
  if (!data) return null;
  return (
    <div>
      <Callout intent="primary" className="hl-mb-sm">
        Permissions cascade from the parent workspace via SpiceDB{" "}
        <Tag minimal>parent_workspace</Tag>
        {data.project_urn ? (
          <>
            {" "}
            plus optional <Tag minimal>parent_project</Tag>
          </>
        ) : null}
        . Create still requires workspace <Tag minimal>approve</Tag>; edit needs SPT{" "}
        <Tag minimal>write</Tag>; delete needs SPT <Tag minimal>approve</Tag>.
      </Callout>
      <FormGroup label="Parent workspace">
        <InputGroup className="hl-mono" readOnly value={data.parent_workspace_urn} />
      </FormGroup>
      {data.project_urn && (
        <FormGroup label="Project scope">
          <InputGroup className="hl-mono" readOnly value={data.project_urn} />
        </FormGroup>
      )}
      <FormGroup label="Your effective permissions">
        <div className="hl-tag-row">
          {(["read", "write", "approve"] as const).map((p) => (
            <Tag key={p} minimal intent={data.permissions[p] ? "success" : "none"}>
              {p}: {data.permissions[p] ? "allowed" : "denied"}
            </Tag>
          ))}
        </div>
      </FormGroup>
      <UrnRow urn={data.urn} />
    </div>
  );
}

export function SharedPropertyTypesTab() {
  const { data } = useSharedPropertyTypes();
  const { data: valueTypes } = useValueTypes();
  const { data: projects = [] } = useProjects();
  const createSharedPropertyType = useCreateSharedPropertyType();
  const updateSharedPropertyType = useUpdateSharedPropertyType();
  const deleteSharedPropertyType = useDeleteSharedPropertyType();
  const [creating, setCreating] = useState(false);
  const [apiName, setApiName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [kind, setKind] = useState<"value_type" | "struct">("value_type");
  const [valueType, setValueType] = useState("");
  const [structFields, setStructFields] = useState<EditableStructField[]>([emptyStructFieldExport("field1")]);
  const [description, setDescription] = useState("");
  const [createMeta, setCreateMeta] = useState<MetaState>(DEFAULT_META);
  const [createProjectUrn, setCreateProjectUrn] = useState("");
  const [editing, setEditing] = useState<SharedPropertyType | null>(null);
  const [editTab, setEditTab] = useState("general");
  const [editDisplayName, setEditDisplayName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editMeta, setEditMeta] = useState<MetaState>(DEFAULT_META);
  const [editProjectUrn, setEditProjectUrn] = useState("");
  const [branching, setBranching] = useState<SharedPropertyType | null>(null);
  const [deleting, setDeleting] = useState<SharedPropertyType | null>(null);
  const [showEphemeral, setShowEphemeral] = useState(false);

  const ephemeralCount = useMemo(
    () => data.filter((spt) => isEphemeralTestName(spt.api_name)).length,
    [data],
  );
  const visibleSpts = useMemo(
    () => (showEphemeral ? data : data.filter((spt) => !isEphemeralTestName(spt.api_name))),
    [data, showEphemeral],
  );

  usePaletteCreateIntent("create-shared-property-type", setCreating);

  function resetCreate() {
    setApiName("");
    setDisplayName("");
    setKind("value_type");
    setValueType("");
    setStructFields([emptyStructFieldExport("field1")]);
    setDescription("");
    setCreateMeta(DEFAULT_META);
    setCreateProjectUrn("");
  }

  function closeCreate() {
    setCreating(false);
    resetCreate();
  }

  const {
    submit: submitCreate,
    error: createError,
    isPending: createPending,
  } = useAsyncAction(async () => {
    const payload = {
      ...metaPayload(createMeta),
      project_urn: createProjectUrn || undefined,
    };
    if (kind === "struct") {
      const draft = emptyProperty(apiName || "struct");
      draft.typeKind = "struct";
      draft.structFields = structFields;
      const { property_types } = serializePropertyEditor([draft]);
      const rule = property_types[draft.name];
      if (!rule || rule.kind !== "struct") {
        throw new Error("Add at least one typed struct field.");
      }
      await createSharedPropertyType.mutateAsync({
        api_name: apiName,
        display_name: displayName,
        struct_properties: rule.properties,
        description: description || undefined,
        ...payload,
      });
    } else {
      await createSharedPropertyType.mutateAsync({
        api_name: apiName,
        display_name: displayName,
        value_type: valueType,
        description: description || undefined,
        ...payload,
      });
    }
    closeCreate();
  }, { successMessage: `Shared property type "${displayName}" created` });

  function openEdit(spt: SharedPropertyType) {
    setEditing(spt);
    setEditTab("general");
    setEditDisplayName(spt.display_name);
    setEditDescription(spt.description ?? "");
    setEditMeta(metaFromSpt(spt));
    setEditProjectUrn(spt.project_urn ?? "");
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    const format = serializeFormat(editMeta);
    const previousProject = editing.project_urn ?? "";
    await updateSharedPropertyType.mutateAsync({
      apiName: editing.api_name,
      body: {
        display_name: editDisplayName,
        description: editDescription,
        visibility: editMeta.visibility,
        render_hints: editMeta.renderHints,
        type_classes: parseTypeClassesInput(editMeta.typeClasses),
        aliases: parseAliasesInput(editMeta.aliases),
        property_format: format ?? undefined,
        clear_property_format: format === null,
        project_urn: editProjectUrn || undefined,
        clear_project_urn: !editProjectUrn && !!previousProject,
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.display_name ?? "Shared property type"}" saved` });

  const {
    submit: submitDelete,
    error: deleteError,
    isPending: deletePending,
  } = useAsyncAction(async () => {
    if (!deleting) return;
    const name = deleting.api_name;
    const result = await deleteSharedPropertyType.mutateAsync(name);
    setDeleting(null);
    const detached = result.detached_object_types ?? [];
    showSuccess(
      detached.length > 0
        ? `Deleted "${name}" and reverted ${detached.length} ObjectType(s): ${detached.join(", ")}`
        : `Deleted shared property "${name}"`,
    );
  });

  useEffect(() => {
    if (!editing) return;
    const latest = data.find((s) => s.api_name === editing.api_name);
    if (latest && latest !== editing) setEditing(latest);
  }, [data, editing]);

  const createReady =
    !!apiName &&
    !!displayName &&
    (kind === "value_type"
      ? !!valueType
      : structFields.some((f) => f.name.trim() && (f.valueType || f.sharedPropertyType)));

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            A canonical, reusable <em>property</em> — API name, display metadata, aliases, and either a Value
            Type or a one-level struct. Metadata is inherited by ObjectTypes that attach it; permissions
            cascade from the workspace via the SPT URN (Foundry parity).
          </>
        }
        createLabel="New shared property type"
        createDisabled={valueTypes.length === 0}
        onCreate={() => setCreating(true)}
        trailing={
          ephemeralCount > 0 ? (
            <Checkbox
              checked={showEphemeral}
              label={`Show test leftovers (${ephemeralCount})`}
              onChange={(e) => setShowEphemeral(e.currentTarget.checked)}
              style={{ marginBottom: 0 }}
            />
          ) : undefined
        }
      />

      {valueTypes.length === 0 && (
        <Callout intent="none" style={{ marginBottom: 12 }}>
          Register a Value Type first — value-typed Shared Property Types wrap one, and struct fields also
          reference Value Types.
        </Callout>
      )}

      <CardGrid minWidth={260}>
        {visibleSpts.map((spt) => (
          <RegistryCard
            key={spt.api_name}
            name={spt.display_name}
            onEdit={() => openEdit(spt)}
            onBranch={() => setBranching(spt)}
            onDelete={() => setDeleting(spt)}
          >
            <div className="hl-tag-row hl-mt-xs">
              <Tag minimal className="hl-mono" icon="globe">
                {spt.api_name}
              </Tag>
              {spt.struct_properties ? (
                <Tag minimal intent="primary">
                  struct ({Object.keys(spt.struct_properties).length})
                </Tag>
              ) : (
                <Tag minimal icon="link">
                  {spt.value_type}
                </Tag>
              )}
              {spt.visibility && spt.visibility !== "normal" && <Tag minimal>{spt.visibility}</Tag>}
              {spt.property_format && <Tag minimal>{spt.property_format.kind}</Tag>}
              {(spt.aliases?.length ?? 0) > 0 && <Tag minimal>{spt.aliases!.length} aliases</Tag>}
              {spt.project_urn && <Tag minimal icon="folder-close">project</Tag>}
            </div>
            {spt.urn && (
              <p className="hl-text-muted-sm hl-mono hl-mt-xs" title={spt.urn}>
                {spt.urn}
              </p>
            )}
            {spt.description && <p className="hl-card-desc">{spt.description}</p>}
          </RegistryCard>
        ))}
        {visibleSpts.length === 0 && (
          <EmptyState actionLabel="New shared property type" onAction={() => setCreating(true)}>
            {data.length === 0
              ? "No shared property types yet."
              : "No durable shared property types — show test leftovers to browse pytest SPTs."}
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New shared property type"
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!createReady}
        onSubmit={() => submitCreate(undefined)}
        style={{ width: 560 }}
      >
        <FormGroup label="API name" helperText="referenced by property_types">
          <InputGroup className="hl-mono" placeholder="startDate" value={apiName} onChange={(e) => setApiName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Display name">
          <InputGroup placeholder="Start date" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Kind">
          <HTMLSelect fill value={kind} onChange={(e) => setKind(e.target.value as "value_type" | "struct")}>
            <option value="value_type">Value type</option>
            <option value="struct">Struct</option>
          </HTMLSelect>
        </FormGroup>
        {kind === "value_type" ? (
          <FormGroup label="Value type (base type)">
            <HTMLSelect fill value={valueType} onChange={(e) => setValueType(e.target.value)}>
              <option value="">Select…</option>
              {valueTypes.map((vt) => (
                <option key={vt.name} value={vt.name}>
                  {vt.name} ({vt.base_type})
                </option>
              ))}
            </HTMLSelect>
          </FormGroup>
        ) : (
          <FormGroup label="Struct fields">
            <div className="hl-struct-fields">
              {structFields.map((field, index) => (
                <div key={index} className="hl-struct-field-row">
                  <InputGroup
                    small
                    className="hl-mono"
                    placeholder="fieldName"
                    value={field.name}
                    onChange={(e) =>
                      setStructFields((fields) =>
                        fields.map((f, i) => (i === index ? { ...f, name: e.target.value } : f)),
                      )
                    }
                  />
                  <HTMLSelect
                    value={field.valueType}
                    onChange={(e) =>
                      setStructFields((fields) =>
                        fields.map((f, i) =>
                          i === index ? { ...f, leafKind: "value_type", valueType: e.target.value } : f,
                        ),
                      )
                    }
                  >
                    <option value="">Value type…</option>
                    {valueTypes.map((vt) => (
                      <option key={vt.name} value={vt.name}>
                        {vt.name}
                      </option>
                    ))}
                  </HTMLSelect>
                  <Button
                    small
                    minimal
                    icon="cross"
                    disabled={structFields.length <= 1}
                    onClick={() => setStructFields((fields) => fields.filter((_, i) => i !== index))}
                  />
                </div>
              ))}
              <Button
                small
                minimal
                icon="add"
                onClick={() =>
                  setStructFields((fields) => [...fields, emptyStructFieldExport(`field${fields.length + 1}`)])
                }
              >
                Add field
              </Button>
            </div>
          </FormGroup>
        )}
        <FormGroup label="Description">
          <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
        <FormGroup
          label="Project"
          helperText="Optional — narrows access to this project's members, additive on top of workspace access"
        >
          <HTMLSelect fill value={createProjectUrn} onChange={(e) => setCreateProjectUrn(e.target.value)}>
            <option value="">No project</option>
            {projects.map((p) => (
              <option key={p.urn} value={p.urn}>
                {p.name}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        <DetailsFields meta={createMeta} onChange={setCreateMeta} />
        <VisibilityField meta={createMeta} onChange={setCreateMeta} />
        <FormatFields meta={createMeta} onChange={setCreateMeta} />
        <InteractionFields meta={createMeta} onChange={setCreateMeta} />
      </RegistryDialog>

      <RegistryDialog
        isOpen={editing !== null}
        title={`Edit ${editing?.api_name ?? ""}`}
        onClose={() => setEditing(null)}
        error={editError}
        isPending={editPending}
        submitLabel="Save"
        submitDisabled={!editDisplayName}
        onSubmit={() => submitEdit(undefined)}
        style={{ width: 640 }}
      >
        <Tabs id="spt-edit-tabs" selectedTabId={editTab} onChange={(id) => setEditTab(String(id))} renderActiveTabPanelOnly>
          <Tab
            id="general"
            title="General"
            panel={
              <div>
                <p className="hl-text-muted-sm hl-mb-sm">
                  API name (<Tag minimal className="hl-mono">{editing?.api_name}</Tag>) and data shape aren&apos;t
                  editable.
                </p>
                {editing?.urn && <UrnRow urn={editing.urn} />}
                <FormGroup label="Display name">
                  <InputGroup value={editDisplayName} onChange={(e) => setEditDisplayName(e.target.value)} />
                </FormGroup>
                <FormGroup label="Description">
                  <InputGroup value={editDescription} onChange={(e) => setEditDescription(e.target.value)} />
                </FormGroup>
                <FormGroup
                  label="Project"
                  helperText="Optional — narrows access to this project's members, additive on top of workspace access"
                >
                  <HTMLSelect fill value={editProjectUrn} onChange={(e) => setEditProjectUrn(e.target.value)}>
                    <option value="">No project</option>
                    {projects.map((p) => (
                      <option key={p.urn} value={p.urn}>
                        {p.name}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Base type">
                  {editing?.struct_properties ? (
                    <Tag minimal intent="primary">
                      struct ({Object.keys(editing.struct_properties).length} fields)
                    </Tag>
                  ) : (
                    <Tag minimal icon="link">
                      {editing?.value_type}
                    </Tag>
                  )}
                </FormGroup>
              </div>
            }
          />
          <Tab
            id="display"
            title="Display"
            panel={
              <div>
                <VisibilityField meta={editMeta} onChange={setEditMeta} />
                <FormatFields meta={editMeta} onChange={setEditMeta} />
              </div>
            }
          />
          <Tab
            id="interaction"
            title="Interaction"
            panel={<InteractionFields meta={editMeta} onChange={setEditMeta} />}
          />
          <Tab id="details" title="Details" panel={<DetailsFields meta={editMeta} onChange={setEditMeta} />} />
          <Tab
            id="usage"
            title="Usage"
            panel={editing ? <UsagePanel apiName={editing.api_name} /> : <span />}
          />
          <Tab
            id="permissions"
            title="Permissions"
            panel={editing ? <PermissionsPanel apiName={editing.api_name} /> : <span />}
          />
        </Tabs>
      </RegistryDialog>

      <RegistryDialog
        isOpen={deleting !== null}
        title={`Delete ${deleting?.api_name ?? ""}`}
        onClose={() => setDeleting(null)}
        error={deleteError}
        isPending={deletePending}
        submitLabel="Delete"
        intent="danger"
        onSubmit={() => submitDelete(undefined)}
      >
        <p>
          Delete shared property <Tag minimal className="hl-mono">{deleting?.api_name}</Tag>? Attached
          ObjectType properties revert to local value_type/struct rules (Foundry-style auto-detach), then
          the shared definition is removed. Requires SPT <Tag minimal>approve</Tag>.
        </p>
        {deleting?.urn && <p className="hl-text-muted-sm hl-mono hl-mb-sm">{deleting.urn}</p>}
        {deleting && <UsagePanel apiName={deleting.api_name} />}
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="shared_property_type"
          resourceName={branching.api_name}
          currentDefinition={{
            display_name: branching.display_name,
            value_type: branching.value_type,
            struct_properties: branching.struct_properties,
            description: branching.description,
            visibility: branching.visibility,
            render_hints: branching.render_hints,
            type_classes: branching.type_classes,
            property_format: branching.property_format,
            aliases: branching.aliases,
            project_urn: branching.project_urn,
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
