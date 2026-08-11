import { Button, FormGroup, HTMLSelect, InputGroup, NumericInput, Tag } from "@blueprintjs/core";
import type { ObjectType, RelationType } from "../../api/knowledge";
import {
  arrayPropertyNames,
  type EditableDerivedProperty,
  emptyDerivedProperty,
  LINK_AGGREGATES,
  linkNamesFromType,
  propertyNamesOfType,
  STRUCT_REDUCERS,
  typeAfterPath,
} from "./derivedEditorUtils";

export function DerivedPropertiesEditor({
  objectType,
  properties,
  selectedName,
  onSelect,
  onChange,
  relationTypes,
  objectTypes,
}: {
  objectType: ObjectType;
  properties: EditableDerivedProperty[];
  selectedName: string | null;
  onSelect: (name: string | null) => void;
  onChange: (next: EditableDerivedProperty[]) => void;
  relationTypes: RelationType[];
  objectTypes: ObjectType[];
}) {
  const selected = properties.find((p) => p.name === selectedName) ?? null;

  function updateSelected(patch: Partial<EditableDerivedProperty>) {
    if (!selected) return;
    onChange(properties.map((p) => (p.name === selected.name ? { ...p, ...patch } : p)));
  }

  function renameSelected(nextName: string) {
    if (!selected) return;
    onChange(properties.map((p) => (p.name === selected.name ? { ...p, name: nextName } : p)));
    onSelect(nextName);
  }

  function addProperty() {
    let n = properties.length + 1;
    let name = `derived${n}`;
    while (properties.some((p) => p.name === name)) {
      n += 1;
      name = `derived${n}`;
    }
    onChange([...properties, emptyDerivedProperty(name)]);
    onSelect(name);
  }

  function removeSelected() {
    if (!selected) return;
    const next = properties.filter((p) => p.name !== selected.name);
    onChange(next);
    onSelect(next[0]?.name ?? null);
  }

  const farType =
    selected?.kind === "link_aggregate"
      ? typeAfterPath(objectType.name, selected.path.filter(Boolean), relationTypes)
      : null;
  const farProperties = farType ? propertyNamesOfType(farType, objectTypes) : [];
  const arrayProps = arrayPropertyNames(objectType);

  return (
    <div className="hl-property-editor">
      <div className="hl-property-editor-list">
        <div className="hl-flex-between hl-mb-xs">
          <span className="hl-section-title">Derived</span>
          <Button small minimal icon="add" onClick={addProperty}>
            Add
          </Button>
        </div>
        {properties.map((p) => (
          <button
            key={p.name}
            type="button"
            className={`hl-property-editor-item${selected?.name === p.name ? " is-selected" : ""}`}
            onClick={() => onSelect(p.name)}
          >
            <span className="hl-mono">{p.name || "(unnamed)"}</span>
            <span className="hl-tag-row">
              <Tag minimal>{p.kind === "function" ? "fn" : p.kind === "link_aggregate" ? "link" : "struct"}</Tag>
              {p.kind === "link_aggregate" && p.path.filter(Boolean).length > 1 && (
                <Tag minimal>{p.path.filter(Boolean).length} hops</Tag>
              )}
            </span>
          </button>
        ))}
        {properties.length === 0 && <p className="hl-text-muted-sm">No derived properties yet.</p>}
      </div>

      <div className="hl-property-editor-form">
        {!selected ? (
          <p className="hl-text-muted">Select or add a derived property (function, link aggregate, or struct reducer).</p>
        ) : (
          <>
            <FormGroup label="API name">
              <InputGroup className="hl-mono" value={selected.name} onChange={(e) => renameSelected(e.target.value)} />
            </FormGroup>
            <FormGroup label="Source kind">
              <HTMLSelect
                fill
                value={selected.kind}
                onChange={(e) => updateSelected({ kind: e.target.value as EditableDerivedProperty["kind"] })}
              >
                <option value="link_aggregate">Linked objects (aggregate)</option>
                <option value="struct_reducer">Struct / array reducer</option>
                <option value="function">Function plugin</option>
              </HTMLSelect>
            </FormGroup>

            {selected.kind === "function" && (
              <FormGroup label="Function name" helperText="Active Function plugin name">
                <InputGroup
                  className="hl-mono"
                  value={selected.functionName}
                  onChange={(e) => updateSelected({ functionName: e.target.value })}
                  placeholder="lifetime_tier"
                />
              </FormGroup>
            )}

            {selected.kind === "link_aggregate" && (
              <>
                <FormGroup
                  label="Link path"
                  helperText="1–3 hops from this ObjectType (Foundry multi-hop derived properties)."
                >
                  {selected.path.map((hop, index) => {
                    const prefix = selected.path.slice(0, index).filter(Boolean);
                    const fromType =
                      index === 0
                        ? objectType.name
                        : typeAfterPath(objectType.name, prefix, relationTypes) ?? objectType.name;
                    const options = linkNamesFromType(fromType, relationTypes);
                    return (
                      <div key={index} className="hl-flex-row hl-mb-xs">
                        <HTMLSelect
                          fill
                          value={hop}
                          onChange={(e) => {
                            const path = [...selected.path];
                            path[index] = e.target.value;
                            updateSelected({ path });
                          }}
                        >
                          <option value="">Select link…</option>
                          {options.map((n) => (
                            <option key={n} value={n}>
                              {n}
                            </option>
                          ))}
                        </HTMLSelect>
                        <Button
                          small
                          minimal
                          icon="cross"
                          disabled={selected.path.length <= 1}
                          onClick={() => updateSelected({ path: selected.path.filter((_, i) => i !== index) })}
                        />
                      </div>
                    );
                  })}
                  {selected.path.length < 3 && (
                    <Button
                      small
                      minimal
                      icon="add"
                      onClick={() => updateSelected({ path: [...selected.path, ""] })}
                    >
                      Add linked object
                    </Button>
                  )}
                  {farType && (
                    <p className="hl-text-muted-sm hl-mt-xs">
                      Final type: <span className="hl-mono">{farType}</span>
                    </p>
                  )}
                </FormGroup>
                <FormGroup label="Aggregation">
                  <HTMLSelect
                    fill
                    value={selected.aggregate}
                    onChange={(e) =>
                      updateSelected({ aggregate: e.target.value as EditableDerivedProperty["aggregate"] })
                    }
                  >
                    {LINK_AGGREGATES.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                {selected.aggregate !== "count" && (
                  <FormGroup label="Property on related type">
                    <HTMLSelect
                      fill
                      value={selected.relatedProperty}
                      onChange={(e) => updateSelected({ relatedProperty: e.target.value })}
                    >
                      <option value="">Select…</option>
                      {farProperties.map((n) => (
                        <option key={n} value={n}>
                          {n}
                        </option>
                      ))}
                    </HTMLSelect>
                  </FormGroup>
                )}
                {(selected.aggregate === "collect_list" || selected.aggregate === "collect_set") && (
                  <FormGroup label="Collect limit">
                    <NumericInput
                      fill
                      min={1}
                      value={selected.collectLimit}
                      onValueChange={(n) => updateSelected({ collectLimit: Number.isFinite(n) && n > 0 ? n : 10 })}
                    />
                  </FormGroup>
                )}
              </>
            )}

            {selected.kind === "struct_reducer" && (
              <>
                <FormGroup label="Array property">
                  <HTMLSelect
                    fill
                    value={selected.arrayProperty}
                    onChange={(e) => updateSelected({ arrayProperty: e.target.value })}
                  >
                    <option value="">Select…</option>
                    {arrayProps.map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Reducer">
                  <HTMLSelect
                    fill
                    value={selected.reducer}
                    onChange={(e) =>
                      updateSelected({ reducer: e.target.value as EditableDerivedProperty["reducer"] })
                    }
                  >
                    {STRUCT_REDUCERS.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="By field" helperText="Required for field-based reducers on struct arrays">
                  <InputGroup
                    className="hl-mono"
                    value={selected.by}
                    onChange={(e) => updateSelected({ by: e.target.value })}
                    placeholder="reviewedAt"
                  />
                </FormGroup>
              </>
            )}

            <Button small intent="danger" minimal icon="trash" onClick={removeSelected}>
              Remove derived property
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
