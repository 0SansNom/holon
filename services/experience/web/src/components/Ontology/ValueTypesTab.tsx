import { useState } from "react";
import { FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import Editor from "@monaco-editor/react";
import { useValueTypes, useCreateValueType, useUpdateValueType } from "../../api/hooks";
import type { ValueType, ValueTypeConstraint } from "../../api/knowledge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryDialog } from "../common/RegistryDialog";
import { usePaletteCreateIntent } from "../../hooks/usePaletteCreateIntent";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { useMonacoEditorTheme } from "../../hooks/useMonacoEditorTheme";
import { BranchesDialog } from "./BranchesDialog";
import { OntologyTabHeader, RegistryCard } from "./OntologyTabLayout";

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

const DEFAULT_CONSTRAINTS = "[]";

function parseConstraintsJson(json: string): ValueTypeConstraint[] {
  try {
    return JSON.parse(json) as ValueTypeConstraint[];
  } catch {
    throw new Error("Constraints must be valid JSON.");
  }
}

export function ValueTypesTab() {
  const monacoTheme = useMonacoEditorTheme();
  const { data } = useValueTypes();
  const createValueType = useCreateValueType();
  const updateValueType = useUpdateValueType();
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");
  const [baseType, setBaseType] = useState<string>("string");
  const [formatRegex, setFormatRegex] = useState("");
  const [constraintsJson, setConstraintsJson] = useState(DEFAULT_CONSTRAINTS);
  const [description, setDescription] = useState("");
  const [editing, setEditing] = useState<ValueType | null>(null);
  const [branching, setBranching] = useState<ValueType | null>(null);

  usePaletteCreateIntent("create-value-type", setCreating);

  function resetCreate() {
    setName("");
    setBaseType("string");
    setFormatRegex("");
    setConstraintsJson(DEFAULT_CONSTRAINTS);
    setDescription("");
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
      constraints: constraints.length > 0 ? constraints : undefined,
      description: description || undefined,
    });
    closeCreate();
  }, { successMessage: `Value type "${name}" created` });

  function openEdit(vt: ValueType) {
    setEditing(vt);
    setFormatRegex(vt.format_regex ?? "");
    setConstraintsJson(JSON.stringify(vt.constraints ?? [], null, 2));
    setDescription(vt.description ?? "");
  }

  const {
    submit: submitEdit,
    error: editError,
    isPending: editPending,
  } = useAsyncAction(async () => {
    if (!editing) return;
    const constraints = parseConstraintsJson(constraintsJson);
    await updateValueType.mutateAsync({
      name: editing.name,
      body: {
        format_regex: editing.base_type === "string" && formatRegex ? formatRegex : null,
        constraints,
        description,
      },
    });
    setEditing(null);
  }, { successMessage: `"${editing?.name ?? "Value type"}" saved` });

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            A named, reusable data type — a base primitive plus an optional format constraint (e.g. "Email" = string +
            a regex). Referenced by a typed property (<code>property_types</code>) or a declarative Action's parameter.
          </>
        }
        createLabel="New value type"
        onCreate={() => setCreating(true)}
      />

      <CardGrid>
        {data.map((vt) => (
          <RegistryCard key={vt.name} name={vt.name} onEdit={() => openEdit(vt)} onBranch={() => setBranching(vt)}>
            <div className="hl-tag-row hl-mt-xs">
              <Tag minimal>{vt.base_type}</Tag>
              {vt.format_regex && (
                <Tag minimal icon="regex" className="hl-mono">
                  {vt.format_regex}
                </Tag>
              )}
              {(vt.constraints ?? []).map((c, i) => (
                <Tag key={i} minimal intent="primary">
                  {c.kind}
                </Tag>
              ))}
            </div>
            {vt.description && <p className="hl-card-desc">{vt.description}</p>}
          </RegistryCard>
        ))}
        {data.length === 0 && (
          <EmptyState actionLabel="New value type" onAction={() => setCreating(true)}>
            No value types yet.
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
        <FormGroup label="Name">
          <InputGroup placeholder="Email" value={name} onChange={(e) => setName(e.target.value)} />
        </FormGroup>
        <FormGroup label="Base type">
          <HTMLSelect fill value={baseType} onChange={(e) => setBaseType(e.target.value)} options={[...BASE_TYPES]} />
        </FormGroup>
        {baseType === "string" && (
          <FormGroup label="Format regex (optional)">
            <InputGroup
              className="hl-mono"
              placeholder="^[^@]+@[^@]+\.[^@]+$"
              value={formatRegex}
              onChange={(e) => setFormatRegex(e.target.value)}
            />
          </FormGroup>
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
        <FormGroup label="Description">
          <InputGroup placeholder="an email address" value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
      </RegistryDialog>

      <RegistryDialog
        isOpen={editing !== null}
        title={`Edit ${editing?.name ?? ""}`}
        onClose={() => setEditing(null)}
        error={editError}
        isPending={editPending}
        submitLabel="Save"
        onSubmit={() => submitEdit(undefined)}
      >
        <p style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
          Base type (<Tag minimal>{editing?.base_type}</Tag>) and name aren't editable — changing either would invalidate
          data already validated against this value type.
        </p>
        {editing?.base_type === "string" && (
          <FormGroup label="Format regex (optional)">
            <InputGroup
              className="hl-mono"
              placeholder="^[^@]+@[^@]+\.[^@]+$"
              value={formatRegex}
              onChange={(e) => setFormatRegex(e.target.value)}
            />
          </FormGroup>
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
        <FormGroup label="Description">
          <InputGroup value={description} onChange={(e) => setDescription(e.target.value)} />
        </FormGroup>
      </RegistryDialog>

      {branching && (
        <BranchesDialog
          kind="value_type"
          resourceName={branching.name}
          currentDefinition={{
            base_type: branching.base_type,
            format_regex: branching.format_regex,
            constraints: branching.constraints,
            description: branching.description,
          }}
          onClose={() => setBranching(null)}
        />
      )}
    </div>
  );
}
