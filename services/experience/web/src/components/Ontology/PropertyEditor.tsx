import { useState } from "react";
import { Button, Checkbox, FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import type { PropertyFormatRule, PropertyRenderHint, SharedPropertyType, ValueType } from "../../api/knowledge";
import { FormattedValue } from "../common/PropertyFormat";
import {
  ALL_RENDER_HINTS,
  applyBulkPropertyPatch,
  type EditableProperty,
  type EditableStructField,
  emptyProperty,
  emptyStructFieldExport,
  parseTypeClassesInput,
  serializePropertyEditor,
  sharedPropertyOptions,
  suggestSharedApiName,
  valueTypeOptions,
} from "./propertyEditorUtils";

const FORMAT_KINDS = ["", "currency", "numeric", "datetime", "principal", "resource-link", "badge"] as const;

function previewRuleFor(prop: EditableProperty): PropertyFormatRule | undefined {
  const { property_formats } = serializePropertyEditor([prop]);
  return property_formats[prop.name];
}

function previewSample(prop: EditableProperty): unknown {
  if (prop.formatKind === "datetime") return new Date().toISOString();
  if (prop.formatKind === "currency" || prop.formatKind === "numeric") {
    if (prop.formatNumericStyle === "percent") return 0.125;
    return 1234.5;
  }
  if (prop.formatKind === "badge") return "active";
  if (prop.formatKind === "principal") return "hl:acme:global:user:jdoe";
  if (prop.formatKind === "resource-link") return "hl:acme:demo:object_type:Customer";
  return "sample";
}

function StructFieldsEditor({
  fields,
  onChange,
  valueTypes,
  sharedPropertyTypes,
}: {
  fields: EditableStructField[];
  onChange: (next: EditableStructField[]) => void;
  valueTypes: ValueType[];
  sharedPropertyTypes: SharedPropertyType[];
}) {
  const vtNames = valueTypeOptions(valueTypes);
  const sptNames = sharedPropertyOptions(sharedPropertyTypes);

  function updateField(index: number, patch: Partial<EditableStructField>) {
    onChange(fields.map((f, i) => (i === index ? { ...f, ...patch } : f)));
  }

  return (
    <div className="hl-struct-fields">
      {fields.map((field, index) => (
        <div key={index} className="hl-struct-field-row">
          <InputGroup
            small
            className="hl-mono"
            placeholder="fieldName"
            value={field.name}
            onChange={(e) => updateField(index, { name: e.target.value })}
          />
          <HTMLSelect
            value={field.leafKind}
            onChange={(e) =>
              updateField(index, {
                leafKind: e.target.value as EditableStructField["leafKind"],
                valueType: "",
                sharedPropertyType: "",
              })
            }
          >
            <option value="value_type">Value type</option>
            <option value="shared_property_type">Shared PT</option>
          </HTMLSelect>
          {field.leafKind === "value_type" ? (
            <HTMLSelect
              value={field.valueType}
              onChange={(e) => updateField(index, { valueType: e.target.value })}
            >
              <option value="">Select…</option>
              {vtNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </HTMLSelect>
          ) : (
            <HTMLSelect
              value={field.sharedPropertyType}
              onChange={(e) => updateField(index, { sharedPropertyType: e.target.value })}
            >
              <option value="">Select…</option>
              {sptNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </HTMLSelect>
          )}
          <Button
            small
            minimal
            icon="cross"
            disabled={fields.length <= 1}
            onClick={() => onChange(fields.filter((_, i) => i !== index))}
          />
        </div>
      ))}
      <Button
        small
        minimal
        icon="add"
        onClick={() => onChange([...fields, emptyStructFieldExport(`field${fields.length + 1}`)])}
      >
        Add field
      </Button>
    </div>
  );
}

/** Split-pane property editor: list + form for the selected property. */
export function ObjectTypePropertyEditor({
  properties,
  selectedName,
  onSelect,
  onChange,
  primaryKey,
  valueTypes,
  sharedPropertyTypes,
  onConvertToShared,
  convertPending,
}: {
  properties: EditableProperty[];
  selectedName: string | null;
  onSelect: (name: string | null) => void;
  onChange: (next: EditableProperty[]) => void;
  primaryKey: string;
  valueTypes: ValueType[];
  sharedPropertyTypes: SharedPropertyType[];
  onConvertToShared?: (property: EditableProperty) => Promise<void>;
  convertPending?: boolean;
}) {
  const selected = properties.find((p) => p.name === selectedName) ?? null;
  const vtNames = valueTypeOptions(valueTypes);
  const sptNames = sharedPropertyOptions(sharedPropertyTypes);
  const [checkedNames, setCheckedNames] = useState<Set<string>>(() => new Set());
  const [bulkVisibility, setBulkVisibility] = useState<EditableProperty["visibility"] | "">("");
  const [bulkFormatKind, setBulkFormatKind] = useState<EditableProperty["formatKind"] | "keep">("keep");

  function toggleChecked(name: string) {
    setCheckedNames((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function updateSelected(patch: Partial<EditableProperty>) {
    if (!selected) return;
    onChange(properties.map((p) => (p.name === selected.name ? { ...p, ...patch } : p)));
  }

  function toggleHint(hint: PropertyRenderHint) {
    if (!selected) return;
    const has = selected.renderHints.includes(hint);
    updateSelected({
      renderHints: has ? selected.renderHints.filter((h) => h !== hint) : [...selected.renderHints, hint],
    });
  }

  function applyBulk() {
    if (checkedNames.size === 0) return;
    const patch: Parameters<typeof applyBulkPropertyPatch>[2] = {};
    if (bulkVisibility) patch.visibility = bulkVisibility;
    if (bulkFormatKind !== "keep") patch.formatKind = bulkFormatKind;
    if (Object.keys(patch).length === 0) return;
    onChange(applyBulkPropertyPatch(properties, checkedNames, patch));
  }

  function renameSelected(nextName: string) {
    if (!selected) return;
    const prevName = selected.name;
    onChange(properties.map((p) => (p.name === prevName ? { ...p, name: nextName } : p)));
    onSelect(nextName);
    setCheckedNames((prev) => {
      if (!prev.has(prevName)) return prev;
      const next = new Set(prev);
      next.delete(prevName);
      next.add(nextName);
      return next;
    });
  }

  function removeSelected() {
    if (!selected || selected.name === primaryKey) return;
    const removed = selected.name;
    const next = properties.filter((p) => p.name !== removed);
    onChange(next);
    onSelect(next[0]?.name ?? null);
    setCheckedNames((prev) => {
      if (!prev.has(removed)) return prev;
      const n = new Set(prev);
      n.delete(removed);
      return n;
    });
  }

  function addProperty() {
    let n = properties.length + 1;
    let name = `property${n}`;
    while (properties.some((p) => p.name === name)) {
      n += 1;
      name = `property${n}`;
    }
    const created = emptyProperty(name);
    onChange([...properties, created]);
    onSelect(name);
  }

  function detachShared() {
    if (!selected || selected.typeKind !== "shared_property_type") return;
    const spt = sharedPropertyTypes.find((s) => s.api_name === selected.sharedPropertyType);
    updateSelected({
      typeKind: "value_type",
      valueType: spt?.value_type ?? "",
      sharedPropertyType: "",
    });
  }

  const canConvert =
    selected?.typeKind === "value_type" &&
    !!selected.valueType &&
    !!onConvertToShared &&
    !sharedPropertyTypes.some((s) => s.api_name === suggestSharedApiName(selected.name));

  return (
    <div className="hl-property-editor">
      <div className="hl-property-editor-list">
        <div className="hl-flex-between hl-mb-xs">
          <span className="hl-section-title">Properties</span>
          <Button small minimal icon="add" onClick={addProperty}>
            Add
          </Button>
        </div>
        {properties.map((p) => (
          <div
            key={`${p.name}-${p.column}`}
            className={`hl-property-editor-item${selected?.name === p.name ? " is-selected" : ""}`}
          >
            <div className="hl-property-editor-item-row">
              <Checkbox
                checked={checkedNames.has(p.name)}
                onChange={() => toggleChecked(p.name)}
                onClick={(e) => e.stopPropagation()}
              />
              <button type="button" className="hl-property-editor-item-btn" onClick={() => onSelect(p.name)}>
                <span className="hl-mono">{p.name || "(unnamed)"}</span>
                <span className="hl-tag-row">
                  {p.name === primaryKey && (
                    <Tag minimal intent="primary">
                      PK
                    </Tag>
                  )}
                  {p.visibility !== "normal" && <Tag minimal>{p.visibility}</Tag>}
                  {p.renderHints.includes("sortable") && <Tag minimal>sort</Tag>}
                  {!p.renderHints.includes("searchable") && <Tag minimal>no-search</Tag>}
                  {p.typeClasses.length > 0 && <Tag minimal>{p.typeClasses[0]}</Tag>}
                  {p.typeKind === "value_type" && <Tag minimal>VT</Tag>}
                  {p.typeKind === "shared_property_type" && (
                    <Tag minimal icon="globe">
                      SPT
                    </Tag>
                  )}
                  {p.typeKind === "struct" && <Tag minimal>struct</Tag>}
                  {p.typeKind === "array" && <Tag minimal>array</Tag>}
                  {p.formatKind && <Tag minimal>{p.formatKind}</Tag>}
                </span>
              </button>
            </div>
          </div>
        ))}
        {properties.length === 0 && <p className="hl-text-muted-sm">No properties mapped yet.</p>}
      </div>

      <div className="hl-property-editor-form">
        {checkedNames.size > 0 && (
          <div className="hl-property-bulk hl-mb-sm">
            <p className="hl-text-muted-sm">
              Bulk edit {checkedNames.size} selected — visibility and format only.
            </p>
            <div className="hl-flex-row hl-mb-xs">
              <HTMLSelect
                value={bulkVisibility}
                onChange={(e) => setBulkVisibility(e.target.value as EditableProperty["visibility"] | "")}
              >
                <option value="">Visibility…</option>
                <option value="prominent">prominent</option>
                <option value="normal">normal</option>
                <option value="hidden">hidden</option>
              </HTMLSelect>
              <HTMLSelect
                value={bulkFormatKind}
                onChange={(e) => setBulkFormatKind(e.target.value as EditableProperty["formatKind"] | "keep")}
              >
                <option value="keep">Format (keep)</option>
                {FORMAT_KINDS.filter(Boolean).map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
                <option value="">clear format</option>
              </HTMLSelect>
              <Button small intent="primary" onClick={applyBulk}>
                Apply
              </Button>
              <Button small minimal onClick={() => setCheckedNames(new Set())}>
                Clear
              </Button>
            </div>
          </div>
        )}
        {!selected ? (
          <p className="hl-text-muted">Select a property to edit its type, format, visibility, and backing column.</p>
        ) : (
          <>
            <FormGroup label="API name" helperText="Programmatic property name (property_mapping key)">
              <InputGroup className="hl-mono" value={selected.name} onChange={(e) => renameSelected(e.target.value)} />
            </FormGroup>
            <FormGroup label="Backing column" helperText="Dataset column this property maps to">
              <InputGroup
                className="hl-mono"
                value={selected.column}
                onChange={(e) => updateSelected({ column: e.target.value })}
              />
            </FormGroup>
            <FormGroup label="Visibility">
              <HTMLSelect
                fill
                value={selected.visibility}
                onChange={(e) => updateSelected({ visibility: e.target.value as EditableProperty["visibility"] })}
              >
                <option value="prominent">prominent</option>
                <option value="normal">normal</option>
                <option value="hidden">hidden</option>
              </HTMLSelect>
            </FormGroup>
            <div className="hl-flex-row hl-mb-sm">
              <Checkbox
                label="Editable"
                checked={selected.editable}
                onChange={() => updateSelected({ editable: !selected.editable })}
              />
              <Checkbox
                label="Required"
                checked={selected.required}
                onChange={() => updateSelected({ required: !selected.required })}
              />
            </div>
            <FormGroup
              label="Render hints"
              helperText="Searchable feeds unified search text; sortable indexes keyword props under props.<name>."
            >
              <div className="hl-flex-row hl-flex-wrap">
                {ALL_RENDER_HINTS.map((hint) => (
                  <Checkbox
                    key={hint}
                    label={hint}
                    checked={selected.renderHints.includes(hint)}
                    onChange={() => toggleHint(hint)}
                  />
                ))}
              </div>
            </FormGroup>
            <FormGroup label="Type classes" helperText="Comma-separated (e.g. priority, important)">
              <InputGroup
                className="hl-mono"
                value={selected.typeClasses.join(", ")}
                onChange={(e) => updateSelected({ typeClasses: parseTypeClassesInput(e.target.value) })}
                placeholder="priority"
              />
            </FormGroup>

            <FormGroup label="Base type kind">
              <HTMLSelect
                fill
                value={selected.typeKind}
                onChange={(e) =>
                  updateSelected({
                    typeKind: e.target.value as EditableProperty["typeKind"],
                    valueType: "",
                    sharedPropertyType: "",
                  })
                }
              >
                <option value="none">(untyped)</option>
                <option value="value_type">Value type</option>
                <option value="shared_property_type">Shared property type</option>
                <option value="struct">Struct</option>
                <option value="array">Array</option>
              </HTMLSelect>
            </FormGroup>

            {selected.typeKind === "value_type" && (
              <>
                <FormGroup label="Value type">
                  <HTMLSelect
                    fill
                    value={selected.valueType}
                    onChange={(e) => updateSelected({ valueType: e.target.value })}
                  >
                    <option value="">Select…</option>
                    {vtNames.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                {canConvert && (
                  <Button
                    small
                    icon="globe"
                    loading={convertPending}
                    className="hl-mb-sm"
                    onClick={() => void onConvertToShared?.(selected)}
                  >
                    Convert to shared property
                  </Button>
                )}
              </>
            )}

            {selected.typeKind === "shared_property_type" && (
              <>
                <FormGroup label="Shared property type" helperText="Attach an existing SPT (Foundry-style share)">
                  <HTMLSelect
                    fill
                    value={selected.sharedPropertyType}
                    onChange={(e) => updateSelected({ sharedPropertyType: e.target.value })}
                  >
                    <option value="">Select…</option>
                    {sptNames.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                {selected.sharedPropertyType && (
                  <Button small minimal icon="disable" className="hl-mb-sm" onClick={detachShared}>
                    Detach shared property
                  </Button>
                )}
              </>
            )}

            {selected.typeKind === "struct" && (
              <FormGroup label="Struct fields" helperText="One nesting level — each field is a Value Type or SPT leaf">
                <StructFieldsEditor
                  fields={selected.structFields}
                  onChange={(structFields) => updateSelected({ structFields })}
                  valueTypes={valueTypes}
                  sharedPropertyTypes={sharedPropertyTypes}
                />
              </FormGroup>
            )}

            {selected.typeKind === "array" && (
              <>
                <FormGroup label="Array element kind">
                  <HTMLSelect
                    fill
                    value={selected.arrayElementKind}
                    onChange={(e) =>
                      updateSelected({
                        arrayElementKind: e.target.value as EditableProperty["arrayElementKind"],
                      })
                    }
                  >
                    <option value="value_type">Value type</option>
                    <option value="shared_property_type">Shared property type</option>
                    <option value="struct">Struct</option>
                  </HTMLSelect>
                </FormGroup>
                {selected.arrayElementKind === "value_type" && (
                  <FormGroup label="Element value type">
                    <HTMLSelect
                      fill
                      value={selected.arrayElementValueType}
                      onChange={(e) => updateSelected({ arrayElementValueType: e.target.value })}
                    >
                      <option value="">Select…</option>
                      {vtNames.map((n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ))}
                    </HTMLSelect>
                  </FormGroup>
                )}
                {selected.arrayElementKind === "shared_property_type" && (
                  <FormGroup label="Element shared property type">
                    <HTMLSelect
                      fill
                      value={selected.arrayElementSharedPropertyType}
                      onChange={(e) => updateSelected({ arrayElementSharedPropertyType: e.target.value })}
                    >
                      <option value="">Select…</option>
                      {sptNames.map((n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ))}
                    </HTMLSelect>
                  </FormGroup>
                )}
                {selected.arrayElementKind === "struct" && (
                  <FormGroup label="Element struct fields">
                    <StructFieldsEditor
                      fields={selected.arrayElementStructFields}
                      onChange={(arrayElementStructFields) => updateSelected({ arrayElementStructFields })}
                      valueTypes={valueTypes}
                      sharedPropertyTypes={sharedPropertyTypes}
                    />
                  </FormGroup>
                )}
              </>
            )}

            <FormGroup label="Value formatting">
              <HTMLSelect
                fill
                value={selected.formatKind}
                onChange={(e) => updateSelected({ formatKind: e.target.value as EditableProperty["formatKind"] })}
              >
                {FORMAT_KINDS.map((k) => (
                  <option key={k || "none"} value={k}>
                    {k || "(none)"}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
            {selected.formatKind === "currency" && (
              <FormGroup label="Currency code">
                <InputGroup
                  value={selected.formatCurrency}
                  onChange={(e) => updateSelected({ formatCurrency: e.target.value })}
                  placeholder="EUR"
                />
              </FormGroup>
            )}
            {selected.formatKind === "numeric" && (
              <>
                <FormGroup label="Numeric style">
                  <HTMLSelect
                    fill
                    value={selected.formatNumericStyle}
                    onChange={(e) =>
                      updateSelected({
                        formatNumericStyle: e.target.value as EditableProperty["formatNumericStyle"],
                      })
                    }
                  >
                    <option value="decimal">decimal</option>
                    <option value="currency">currency</option>
                    <option value="percent">percent</option>
                    <option value="unit">unit</option>
                  </HTMLSelect>
                </FormGroup>
                {selected.formatNumericStyle === "currency" && (
                  <FormGroup label="Currency code">
                    <InputGroup
                      value={selected.formatCurrency}
                      onChange={(e) => updateSelected({ formatCurrency: e.target.value })}
                    />
                  </FormGroup>
                )}
                {selected.formatNumericStyle === "unit" && (
                  <FormGroup label="Unit" helperText="Intl unit identifier (e.g. kilometer, percent)">
                    <InputGroup
                      value={selected.formatUnit}
                      onChange={(e) => updateSelected({ formatUnit: e.target.value })}
                      placeholder="kilometer"
                    />
                  </FormGroup>
                )}
                <FormGroup label="Notation">
                  <HTMLSelect
                    fill
                    value={selected.formatNotation}
                    onChange={(e) =>
                      updateSelected({
                        formatNotation: e.target.value as EditableProperty["formatNotation"],
                      })
                    }
                  >
                    <option value="standard">standard</option>
                    <option value="compact">compact</option>
                    <option value="scientific">scientific</option>
                    <option value="engineering">engineering</option>
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Max fraction digits">
                  <InputGroup
                    value={selected.formatMaxFractionDigits}
                    onChange={(e) => updateSelected({ formatMaxFractionDigits: e.target.value })}
                    placeholder="(default)"
                  />
                </FormGroup>
              </>
            )}
            {selected.formatKind === "datetime" && (
              <FormGroup label="Datetime style">
                <HTMLSelect
                  fill
                  value={selected.formatDatetimeStyle}
                  onChange={(e) =>
                    updateSelected({
                      formatDatetimeStyle: e.target.value as EditableProperty["formatDatetimeStyle"],
                    })
                  }
                >
                  <option value="date">date</option>
                  <option value="datetime-long">datetime-long</option>
                  <option value="datetime-short">datetime-short</option>
                  <option value="iso8601">iso8601</option>
                  <option value="relative">relative</option>
                  <option value="time">time</option>
                </HTMLSelect>
              </FormGroup>
            )}
            {selected.formatKind === "resource-link" && (
              <FormGroup label="Resource type">
                <HTMLSelect
                  fill
                  value={selected.formatResourceType}
                  onChange={(e) =>
                    updateSelected({
                      formatResourceType: e.target.value as EditableProperty["formatResourceType"],
                    })
                  }
                >
                  <option value="object-type">object-type</option>
                  <option value="application">application</option>
                </HTMLSelect>
              </FormGroup>
            )}
            {selected.formatKind === "badge" && (
              <p className="hl-text-muted-sm">Badge color maps are preserved from the existing rule on propose.</p>
            )}
            {selected.formatKind && (
              <div className="hl-format-preview hl-mb-sm">
                <span className="hl-text-muted-sm">Preview</span>
                <div className="hl-format-preview-value">
                  <FormattedValue rule={previewRuleFor(selected)} value={previewSample(selected)} />
                </div>
              </div>
            )}

            <Button
              small
              intent="danger"
              minimal
              icon="trash"
              disabled={selected.name === primaryKey}
              onClick={removeSelected}
            >
              Remove property
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
