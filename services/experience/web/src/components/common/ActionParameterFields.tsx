import { Suspense } from "react";
import { FormGroup, InputGroup, MenuItem, Spinner, Switch } from "@blueprintjs/core";
import { Suggest } from "@blueprintjs/select";
import { useEvaluateObjectSet, useObjects, useValueTypes } from "../../api/hooks";
import type { ActionParameter, ActionParameterSection } from "../../api/knowledge";
import { prefillActionParameters } from "../ObjectExplorer/actionParameterPrefill";
import { coerce } from "./actionParameterUtils";

function instanceLabel(row: Record<string, unknown>): string {
  const id = String(row.id ?? row.Id ?? "");
  for (const key of ["name", "title", "email", "displayName"]) {
    if (typeof row[key] === "string" && row[key]) return `${id} — ${row[key]}`;
  }
  return id;
}

function ObjectReferenceFieldInner({
  objectType,
  objectSet,
  value,
  onChange,
}: {
  objectType: string;
  objectSet?: string;
  value: unknown;
  onChange: (value: unknown, row: Record<string, unknown> | null) => void;
}) {
  const { data: allRows = [] } = useObjects(objectSet ? "" : objectType);
  const { data: setEval } = useEvaluateObjectSet(objectSet ?? "", !!objectSet);
  const items = (objectSet ? setEval?.data : allRows) ?? [];
  const selected = items.find((r: Record<string, unknown>) => String(r.id) === String(value)) ?? null;

  return (
    <Suggest<Record<string, unknown>>
      items={items}
      itemPredicate={(query, item) => instanceLabel(item).toLowerCase().includes(query.toLowerCase())}
      itemRenderer={(item, { handleClick, modifiers }) => (
        <MenuItem key={String(item.id)} text={instanceLabel(item)} active={modifiers.active} onClick={handleClick} />
      )}
      inputValueRenderer={(item) => instanceLabel(item)}
      onItemSelect={(item) => onChange(item.id, item)}
      selectedItem={selected}
      noResults={<MenuItem disabled text={objectSet ? `No matches in set ${objectSet}` : "No matches"} />}
      popoverProps={{ minimal: true }}
    />
  );
}

function ObjectReferenceField({
  objectType,
  objectSet,
  value,
  onChange,
}: {
  objectType: string;
  objectSet?: string;
  value: unknown;
  onChange: (value: unknown, row: Record<string, unknown> | null) => void;
}) {
  return (
    <Suspense fallback={<Spinner size={16} />}>
      <ObjectReferenceFieldInner objectType={objectType} objectSet={objectSet} value={value} onChange={onChange} />
    </Suspense>
  );
}

export function ActionParameterFields({
  parameters,
  values,
  onChange,
  sections,
  currentObjectId,
  currentObject,
}: {
  parameters: ActionParameter[];
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
  sections?: ActionParameterSection[];
  currentObjectId?: string | null;
  currentObject?: Record<string, unknown> | null;
}) {
  const { data: valueTypes = [] } = useValueTypes();

  if (parameters.length === 0) return null;

  function setValue(name: string, value: unknown) {
    onChange({ ...values, [name]: value });
  }

  function setObjectReference(name: string, value: unknown, row: Record<string, unknown> | null) {
    const base = { ...values, [name]: value };
    const dependents = prefillActionParameters(parameters, {
      currentObjectId,
      currentObject,
      objectsByParameter: { [name]: row },
      onlyFromObjectParameter: name,
    });
    onChange({ ...base, ...dependents });
  }

  function renderField(p: ActionParameter) {
    const value = values[p.name];
    const label = p.required ? `${p.name} (required)` : p.name;

    if (p.kind === "object_reference" && p.object_type) {
      return (
        <FormGroup
          key={p.name}
          label={label}
          helperText={p.object_set ? `Filtered by Object Set ${p.object_set}` : undefined}
        >
          <ObjectReferenceField
            objectType={p.object_type}
            objectSet={p.object_set}
            value={value}
            onChange={(v, row) => setObjectReference(p.name, v, row)}
          />
        </FormGroup>
      );
    }

    const valueType = valueTypes.find((vt) => vt.name === p.value_type);
    const baseType = valueType?.base_type;

    if (baseType === "boolean") {
      return (
        <FormGroup key={p.name} label={label}>
          <Switch checked={Boolean(value)} onChange={(e) => setValue(p.name, e.currentTarget.checked)} />
        </FormGroup>
      );
    }

    return (
      <FormGroup key={p.name} label={label}>
        <InputGroup value={value !== undefined ? String(value) : ""} onChange={(e) => setValue(p.name, coerce(e.target.value, baseType))} />
      </FormGroup>
    );
  }

  const parametersByName = new Map(parameters.map((p) => [p.name, p]));
  const groupedNames = new Set((sections ?? []).flatMap((s) => s.parameter_names));
  const ungrouped = parameters.filter((p) => !groupedNames.has(p.name));

  return (
    <>
      {ungrouped.map(renderField)}
      {(sections ?? []).map((section) => {
        const sectionParams = section.parameter_names
          .map((n) => parametersByName.get(n))
          .filter((p): p is ActionParameter => p !== undefined);
        if (sectionParams.length === 0) return null;
        return (
          <div key={section.name}>
            <div
              className="hl-text-muted"
              style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: 0.4, marginTop: 12, marginBottom: 4 }}
            >
              {section.name}
            </div>
            {sectionParams.map(renderField)}
          </div>
        );
      })}
    </>
  );
}
