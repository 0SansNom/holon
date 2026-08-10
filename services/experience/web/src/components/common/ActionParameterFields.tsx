import { Suspense } from "react";
import { FormGroup, InputGroup, MenuItem, Spinner, Switch } from "@blueprintjs/core";
import { Suggest } from "@blueprintjs/select";
import { useObjects, useValueTypes } from "../../api/hooks";
import type { ActionParameter, ActionParameterSection } from "../../api/knowledge";
import { coerce } from "./actionParameterUtils";

// A short, readable label for a candidate instance in the picker below —
// its id plus the first of a few common human-readable fields it has, so
// "12" doesn't stand alone next to a dozen identical-looking rows.
function instanceLabel(row: Record<string, unknown>): string {
  const id = String(row.id ?? row.Id ?? "");
  for (const key of ["name", "title", "email", "displayName"]) {
    if (typeof row[key] === "string" && row[key]) return `${id} — ${row[key]}`;
  }
  return id;
}

function ObjectReferenceFieldInner({
  objectType,
  value,
  onChange,
}: {
  objectType: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const { data: rows } = useObjects(objectType);
  const items = rows;
  const selected = items.find((r) => String(r.id) === String(value)) ?? null;

  return (
    <Suggest<Record<string, unknown>>
      items={items}
      itemPredicate={(query, item) => instanceLabel(item).toLowerCase().includes(query.toLowerCase())}
      itemRenderer={(item, { handleClick, modifiers }) => (
        <MenuItem key={String(item.id)} text={instanceLabel(item)} active={modifiers.active} onClick={handleClick} />
      )}
      inputValueRenderer={(item) => instanceLabel(item)}
      onItemSelect={(item) => onChange(item.id)}
      selectedItem={selected}
      noResults={<MenuItem disabled text="No matches" />}
      popoverProps={{ minimal: true }}
    />
  );
}

function ObjectReferenceField({
  objectType,
  value,
  onChange,
}: {
  objectType: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  return (
    <Suspense fallback={<Spinner size={16} />}>
      <ObjectReferenceFieldInner objectType={objectType} value={value} onChange={onChange} />
    </Suspense>
  );
}

// Shared by every declarative Action invocation dialog (`ObjectDetailPage.tsx`,
// `ObjectAppView.tsx`) — previously neither collected `parameters` at all,
// so any Action Type with a *required* parameter simply couldn't be
// invoked from the UI. One input per declared parameter, type-coerced by
// the referenced Value Type's `base_type` on change (not left as a raw
// string) so `ontology.validate_value`'s server-side type check doesn't
// reject a well-formed submission.
//
// `sections` (Configure/Sections, optional) is purely a display grouping —
// `values`/`onChange` stay a flat `Record<string, unknown>` regardless, the
// same shape submitted whether or not any grouping was ever declared. A
// parameter not named in any section renders ungrouped, at the top, same
// as every Action Type had before this existed.
export function ActionParameterFields({
  parameters,
  values,
  onChange,
  sections,
}: {
  parameters: ActionParameter[];
  values: Record<string, unknown>;
  onChange: (values: Record<string, unknown>) => void;
  sections?: ActionParameterSection[];
}) {
  const { data: valueTypes = [] } = useValueTypes();

  if (parameters.length === 0) return null;

  function setValue(name: string, value: unknown) {
    onChange({ ...values, [name]: value });
  }

  function renderField(p: ActionParameter) {
    const value = values[p.name];
    const label = p.required ? `${p.name} (required)` : p.name;

    if (p.kind === "object_reference" && p.object_type) {
      return (
        <FormGroup key={p.name} label={label}>
          <ObjectReferenceField objectType={p.object_type} value={value} onChange={(v) => setValue(p.name, v)} />
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
