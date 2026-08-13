import { useMemo, useState } from "react";
import { Callout, Checkbox, FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import Editor from "@monaco-editor/react";
import {
  useValueTypes,
  useCreateValueType,
  useUpdateValueType,
  useDeprecateValueType,
  useValueTypeRevisions,
  useValueTypePermissions,
} from "../../api/hooks";
import type { ValueType, ValueTypeConstraint } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useMonacoEditorTheme } from "../../hooks/useMonacoEditorTheme";
import { BranchesDialog } from "./BranchesDialog";
import { isEphemeralTestName } from "./ephemeralResources";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";
import { REGISTRY_LIFECYCLE_STATUSES } from "./lifecycleUtils";

const BASE_TYPES = [
  "string",
  "integer",
  "double",
  "boolean",
  "date",
  "timestamp",
  "short",
  "byte",
  "long",
  "decimal",
  "float",
  "geopoint",
  "geoshape",
  "vector",
] as const;

const LIFECYCLE_STATUSES = REGISTRY_LIFECYCLE_STATUSES;
const DEFAULT_CONSTRAINTS = "[]";

function parseConstraintsJson(json: string): ValueTypeConstraint[] {
  try {
    return JSON.parse(json) as ValueTypeConstraint[];
  } catch {
    throw new Error("Constraints must be valid JSON.");
  }
}

function ValueTypeRevisionsPanel({ name }: { name: string }) {
  const { data, isLoading } = useValueTypeRevisions(name, !!name);
  if (isLoading) return <p className="hl-text-muted-sm">Loading revisions…</p>;
  if (!data?.length) return null;
  return (
    <div className="hl-mt-sm">
      <div className="hl-section-title hl-mb-xs">Versions</div>
      <div className="hl-tag-row">
        {data.map((rev) => (
          <Tag key={rev.version} minimal intent={rev.version === data[0]?.version ? "primary" : "none"}>
            v{rev.version}
          </Tag>
        ))}
      </div>
      <p className="hl-text-muted-sm hl-mt-xs">
        Constraint/format changes bump the version; metadata edits do not. Consumers keep resolving by name →
        latest.
      </p>
    </div>
  );
}

function ValueTypePermissionsPanel({ name }: { name: string }) {
  const { data, isLoading, error } = useValueTypePermissions(name, !!name);
  if (isLoading) return <p className="hl-text-muted-sm">Loading permissions…</p>;
  if (error) return <Callout intent="danger">Could not load permissions.</Callout>;
  if (!data) return null;
  return (
    <div className="hl-mt-sm">
      <div className="hl-section-title hl-mb-xs">Permissions</div>
      <Callout intent="primary" className="hl-mb-sm">
        SpiceDB <Tag minimal>value_type</Tag> cascades from workspace; optional project import adds{" "}
        <Tag minimal>parent_project</Tag>.
      </Callout>
      <FormGroup label="URN">
        <InputGroup className="hl-mono" readOnly value={data.urn} />
      </FormGroup>
      {data.project_urn && (
        <FormGroup label="Project scope">
          <InputGroup className="hl-mono" readOnly value={data.project_urn} />
        </FormGroup>
      )}
      <div className="hl-tag-row">
        {(["read", "write", "approve"] as const).map((p) => (
          <Tag key={p} minimal intent={data.permissions[p] ? "success" : "none"}>
            {p}: {data.permissions[p] ? "yes" : "no"}
          </Tag>
        ))}
      </div>
    </div>
  );
}

export function ValueTypesTab() {
  const monacoTheme = useMonacoEditorTheme();
  const { data } = useValueTypes();
  const createValueType = useCreateValueType();
  const updateValueType = useUpdateValueType();
  const deprecateValueType = useDeprecateValueType();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [apiName, setApiName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [baseType, setBaseType] = useState<string>("string");
  const [formatRegex, setFormatRegex] = useState("");
  const [constraintsJson, setConstraintsJson] = useState(DEFAULT_CONSTRAINTS);
  const [description, setDescription] = useState("");
  const [exampleValue, setExampleValue] = useState("");
  const [lifecycleStatus, setLifecycleStatus] = useState<string>("experimental");
  const [deprecationReason, setDeprecationReason] = useState("");
  const [deprecationDeadline, setDeprecationDeadline] = useState("");
  const [replacementUrn, setReplacementUrn] = useState("");
  const [formatRegexMatch, setFormatRegexMatch] = useState<string>("full");
  const [projectUrn, setProjectUrn] = useState("");
  const [editing, setEditing] = useState<ValueType | null>(null);
  const [branching, setBranching] = useState<ValueType | null>(null);
  const [showEphemeral, setShowEphemeral] = useState(false);

  const ephemeralCount = useMemo(
    () => data.filter((vt) => isEphemeralTestName(vt.name)).length,
    [data],
  );
  const visibleValueTypes = useMemo(
    () => (showEphemeral ? data : data.filter((vt) => !isEphemeralTestName(vt.name))),
    [data, showEphemeral],
  );

  usePaletteCreateIntent("create-value-type", setCreating);

  function resetCreate() {
    setName("");
    setApiName("");
    setDisplayName("");
    setBaseType("string");
    setFormatRegex("");
    setConstraintsJson(DEFAULT_CONSTRAINTS);
    setDescription("");
    setExampleValue("");
    setLifecycleStatus("experimental");
    setFormatRegexMatch("full");
    setProjectUrn("");
    setDeprecationReason("");
    setDeprecationDeadline("");
    setReplacementUrn("");
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
    const constraints = parseConstraintsJson(constraintsJson);
    await createValueType.mutateAsync({
      name,
      base_type: baseType,
      format_regex: baseType === "string" && formatRegex ? formatRegex : undefined,
      format_regex_match: baseType === "string" ? formatRegexMatch : undefined,
      constraints: constraints.length > 0 ? constraints : undefined,
      description: description || undefined,
      api_name: apiName || undefined,
      display_name: displayName || undefined,
      example_value: exampleValue || undefined,
      lifecycle_status: lifecycleStatus,
      project_urn: projectUrn || undefined,
      deprecation_reason: lifecycleStatus === "deprecated" ? deprecationReason : undefined,
      deprecation_deadline: lifecycleStatus === "deprecated" ? deprecationDeadline || undefined : undefined,
      replacement_urn: lifecycleStatus === "deprecated" ? replacementUrn || undefined : undefined,
    });
    closeCreate();
  }, { successMessage: `Value type "${name}" created` });

  function openEdit(vt: ValueType) {
    setEditing(vt);
    setApiName(vt.api_name || vt.name);
    setDisplayName(vt.display_name || vt.name);
    setFormatRegex(vt.format_regex ?? "");
    setConstraintsJson(JSON.stringify(vt.constraints ?? [], null, 2));
    setDescription(vt.description ?? "");
    setExampleValue(vt.example_value ?? "");
    setLifecycleStatus(vt.lifecycle_status ?? "experimental");
    setDeprecationReason(vt.deprecation_reason ?? "");
    setDeprecationDeadline((vt.deprecation_deadline ?? "").toString().slice(0, 10));
    setReplacementUrn(vt.replacement_urn ?? "");
    setFormatRegexMatch(vt.format_regex_match ?? "full");
    setProjectUrn(vt.project_urn ?? "");
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    const constraints = parseConstraintsJson(constraintsJson);
    const previousProject = editing.project_urn ?? "";
    await updateValueType.mutateAsync({
      name: editing.name,
      body: {
        format_regex: editing.base_type === "string" ? formatRegex || "" : "",
        format_regex_match: editing.base_type === "string" ? formatRegexMatch : undefined,
        constraints,
        description,
        api_name: apiName || editing.name,
        display_name: displayName || editing.name,
        example_value: exampleValue || null,
        clear_example_value: !exampleValue,
        lifecycle_status: lifecycleStatus,
        deprecation_reason: lifecycleStatus === "deprecated" ? deprecationReason : undefined,
        deprecation_deadline: lifecycleStatus === "deprecated" ? deprecationDeadline || undefined : undefined,
        replacement_urn: lifecycleStatus === "deprecated" ? replacementUrn || undefined : undefined,
        project_urn: projectUrn || undefined,
        clear_project_urn: !projectUrn && !!previousProject,
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Value type"}" saved` });

  const {
    submit: submitDeprecate,
    error: deprecateError,
    isPending: deprecatePending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    if (!deprecationReason.trim() || !deprecationDeadline) {
      throw new Error("Deprecation requires a reason and deadline (YYYY-MM-DD).");
    }
    await deprecateValueType.mutateAsync({
      name: editing.name,
      body: {
        deprecation_reason: deprecationReason.trim(),
        deprecation_deadline: deprecationDeadline,
        replacement_urn: replacementUrn.trim() || undefined,
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Value type"}" deprecated` });

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            A named, reusable data type — a base primitive plus optional constraints (e.g. Email = string + regex).
            Constraint changes bump a version; property/Action refs keep resolving by name to the latest.
          </>
        }
        createLabel="New value type"
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

      <CardGrid>
        {visibleValueTypes.map((vt) => (
          <RegistryCard key={vt.name} name={vt.display_name || vt.name} onEdit={() => openEdit(vt)} onBranch={() => setBranching(vt)}>
            <div className="hl-tag-row hl-mt-xs">
              <Tag minimal>{vt.base_type}</Tag>
              <Tag minimal>v{vt.version ?? 1}</Tag>
              <Tag minimal intent={vt.lifecycle_status === "deprecated" ? "warning" : "none"}>
                {vt.lifecycle_status ?? "experimental"}
              </Tag>
              {vt.api_name && vt.api_name !== vt.name && (
                <Tag minimal className="hl-mono">
                  {vt.api_name}
                </Tag>
              )}
              {vt.format_regex && (
                <Tag minimal icon="regex" className="hl-mono">
                  {vt.format_regex_match === "substring" ? "⊂ " : ""}
                  {vt.format_regex}
                </Tag>
              )}
              {vt.project_urn && (
                <Tag minimal icon="folder-close">
                  project
                </Tag>
              )}
              {(vt.constraints ?? []).map((c, i) => (
                <Tag key={i} minimal intent="primary">
                  {c.kind}
                </Tag>
              ))}
            </div>
            {vt.description && <p className="hl-card-desc">{vt.description}</p>}
            {vt.example_value && (
              <p className="hl-text-muted-sm hl-mono">e.g. {vt.example_value}</p>
            )}
          </RegistryCard>
        ))}
        {visibleValueTypes.length === 0 && (
          <EmptyState actionLabel="New value type" onAction={() => setCreating(true)}>
            {data.length === 0
              ? "No value types yet."
              : "No durable value types — show test leftovers to browse pytest types."}
          </EmptyState>
        )}
      </CardGrid>

      <RegistryDialog
        isOpen={creating}
        title="New value type"
        onClose={closeCreate}
        error={createError}
        isPending={createPending}
        submitLabel="Create"
        submitDisabled={!name}
        onSubmit={() => submitCreate(undefined)}
      >
        <FormGroup label="Name (registry key)">
          <InputGroup placeholder="Email" value={name} onChange={(e) => setName(e.target.value)} />
        </FormGroup>
        <FormGroup label="API name" helperText="Defaults to name">
          <InputGroup className="hl-mono" placeholder="Email" value={apiName} onChange={(e) => setApiName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Display name">
          <InputGroup placeholder="Email address" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Base type">
          <HTMLSelect fill value={baseType} onChange={(e) => setBaseType(e.target.value)} options={[...BASE_TYPES]} />
        </FormGroup>
        {baseType === "string" && (
          <>
            <FormGroup label="Format regex (optional)">
              <InputGroup
                className="hl-mono"
                placeholder="^[^@]+@[^@]+\.[^@]+$"
                value={formatRegex}
                onChange={(e) => setFormatRegex(e.target.value)}
              />
            </FormGroup>
            <FormGroup label="Regex match" helperText="Foundry: full string or substring">
              <HTMLSelect fill value={formatRegexMatch} onChange={(e) => setFormatRegexMatch(e.target.value)}>
                <option value="full">full</option>
                <option value="substring">substring</option>
              </HTMLSelect>
            </FormGroup>
          </>
        )}
        <FormGroup
          label="Constraints (optional)"
          helperText={
            <span className="hl-mono">
              [{"{"}kind: "enum"|"range"|"rid"|"uuid", ...{"}"}, ...]
            </span>
          }
        >
          <Editor
            height="90px"
            defaultLanguage="json"
            theme={monacoTheme}
            value={constraintsJson}
            onChange={(v) => setConstraintsJson(v ?? "")}
            options={{ minimap: { enabled: false }, fontSize: 12 }}
          />
        </FormGroup>
        <FormGroup label="Example preview (optional)">
          <InputGroup className="hl-mono" placeholder="user@example.com" value={exampleValue} onChange={(e) => setExampleValue(e.target.value)} />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup placeholder="an email address" value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
        <FormGroup label="Status">
          <HTMLSelect fill value={lifecycleStatus} onChange={(e) => setLifecycleStatus(e.target.value)}>
            {LIFECYCLE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        {lifecycleStatus === "deprecated" && (
          <>
            <FormGroup label="Deprecation reason">
              <InputGroup value={deprecationReason} onChange={(e) => setDeprecationReason(e.target.value)} />
            </FormGroup>
            <FormGroup label="Deprecation deadline">
              <InputGroup type="date" value={deprecationDeadline} onChange={(e) => setDeprecationDeadline(e.target.value)} />
            </FormGroup>
            <FormGroup label="Replacement URN (optional)">
              <InputGroup className="hl-mono" value={replacementUrn} onChange={(e) => setReplacementUrn(e.target.value)} />
            </FormGroup>
          </>
        )}
        <FormGroup label="Project URN (optional)" helperText="Import into a project scope (SpiceDB parent_project)">
          <InputGroup
            className="hl-mono"
            placeholder="hl:acme:global:project:…"
            value={projectUrn}
            onChange={(e) => setProjectUrn(e.target.value)}
          />
        </FormGroup>
      </RegistryDialog>

      <RegistryDialog
        isOpen={editing !== null}
        title={`Edit ${editing?.name ?? ""}`}
        onClose={() => setEditing(null)}
        error={editError || deprecateError}
        isPending={editPending || deprecatePending}
        submitLabel="Save"
        onSubmit={() => submitEdit(undefined)}
        footerStart={
          editing && editing.lifecycle_status !== "deprecated" ? (
            <button type="button" className="bp5-button" disabled={deprecatePending} onClick={() => submitDeprecate(undefined)}>
              Deprecate
            </button>
          ) : undefined
        }
      >
        <Callout intent="primary" className="hl-mb-sm">
          Base type (<Tag minimal>{editing?.base_type}</Tag>) and registry name aren't editable. Changing
          constraints or format regex bumps to <Tag minimal>v{(editing?.version ?? 1) + 1}</Tag>.
        </Callout>
        <FormGroup label="API name">
          <InputGroup className="hl-mono" value={apiName} onChange={(e) => setApiName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Display name">
          <InputGroup value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
        </FormGroup>
        {editing?.base_type === "string" && (
          <>
            <FormGroup label="Format regex (optional)">
              <InputGroup
                className="hl-mono"
                placeholder="^[^@]+@[^@]+\.[^@]+$"
                value={formatRegex}
                onChange={(e) => setFormatRegex(e.target.value)}
              />
            </FormGroup>
            <FormGroup label="Regex match">
              <HTMLSelect fill value={formatRegexMatch} onChange={(e) => setFormatRegexMatch(e.target.value)}>
                <option value="full">full</option>
                <option value="substring">substring</option>
              </HTMLSelect>
            </FormGroup>
          </>
        )}
        <FormGroup
          label="Constraints"
          helperText={
            <span className="hl-mono">
              [{"{"}kind: "enum"|"range"|"rid"|"uuid", ...{"}"}, ...]
            </span>
          }
        >
          <Editor
            height="90px"
            defaultLanguage="json"
            theme={monacoTheme}
            value={constraintsJson}
            onChange={(v) => setConstraintsJson(v ?? "")}
            options={{ minimap: { enabled: false }, fontSize: 12 }}
          />
        </FormGroup>
        <FormGroup label="Example preview">
          <InputGroup className="hl-mono" value={exampleValue} onChange={(e) => setExampleValue(e.target.value)} />
        </FormGroup>
        <FormGroup label="Description">
          <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
        <FormGroup label="Status">
          <HTMLSelect fill value={lifecycleStatus} onChange={(e) => setLifecycleStatus(e.target.value)}>
            {LIFECYCLE_STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </HTMLSelect>
        </FormGroup>
        {(lifecycleStatus === "deprecated" || editing?.lifecycle_status === "deprecated") && (
          <>
            <FormGroup label="Deprecation reason">
              <InputGroup value={deprecationReason} onChange={(e) => setDeprecationReason(e.target.value)} />
            </FormGroup>
            <FormGroup label="Deprecation deadline">
              <InputGroup type="date" value={deprecationDeadline} onChange={(e) => setDeprecationDeadline(e.target.value)} />
            </FormGroup>
            <FormGroup label="Replacement URN (optional)">
              <InputGroup className="hl-mono" value={replacementUrn} onChange={(e) => setReplacementUrn(e.target.value)} />
            </FormGroup>
          </>
        )}
        <FormGroup label="Project URN (optional)">
          <InputGroup className="hl-mono" value={projectUrn} onChange={(e) => setProjectUrn(e.target.value)} />
        </FormGroup>
        {editing && <ValueTypeRevisionsPanel name={editing.name} />}
        {editing && <ValueTypePermissionsPanel name={editing.name} />}
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="value_type"
          resourceName={branching.name}
          currentDefinition={{
            base_type: branching.base_type,
            format_regex: branching.format_regex,
            format_regex_match: branching.format_regex_match,
            constraints: branching.constraints,
            description: branching.description,
            api_name: branching.api_name,
            display_name: branching.display_name,
            example_value: branching.example_value,
            lifecycle_status: branching.lifecycle_status,
            project_urn: branching.project_urn,
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
