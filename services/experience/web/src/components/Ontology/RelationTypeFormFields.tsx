import { FormGroup, HTMLSelect, InputGroup } from "@blueprintjs/core";
import { CARDINALITIES, STORAGE_KINDS, VISIBILITIES } from "./relationTypeConstants";
import { REGISTRY_LIFECYCLE_STATUSES } from "./lifecycleUtils";
import type { RelationTypeFormState } from "./relationTypeForm";

export function RelationTypeFormFields({
  value,
  onChange,
  objectTypeNames,
  projects,
}: {
  value: RelationTypeFormState;
  onChange: (patch: Partial<RelationTypeFormState>) => void;
  objectTypeNames: string[];
  projects: Array<{ urn: string; name: string }>;
}) {
  return (
    <>
      <FormGroup label="Target property">
        <InputGroup value={value.targetProperty} onChange={(e) => onChange({ targetProperty: e.target.value })} />
      </FormGroup>
      <FormGroup label="Cardinality">
        <HTMLSelect fill value={value.cardinality} onChange={(e) => onChange({ cardinality: e.target.value })}>
          {CARDINALITIES.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </HTMLSelect>
      </FormGroup>
      <FormGroup label="Storage kind">
        <HTMLSelect fill value={value.storageKind} onChange={(e) => onChange({ storageKind: e.target.value })}>
          {STORAGE_KINDS.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </HTMLSelect>
      </FormGroup>
      {value.storageKind === "join_dataset" && (
        <>
          <FormGroup label="Join dataset URN">
            <InputGroup value={value.joinDatasetUrn} onChange={(e) => onChange({ joinDatasetUrn: e.target.value })} />
          </FormGroup>
          <FormGroup label="Join source column">
            <InputGroup
              value={value.joinSourceColumn}
              onChange={(e) => onChange({ joinSourceColumn: e.target.value })}
            />
          </FormGroup>
          <FormGroup label="Join target column">
            <InputGroup
              value={value.joinTargetColumn}
              onChange={(e) => onChange({ joinTargetColumn: e.target.value })}
            />
          </FormGroup>
        </>
      )}
      {value.storageKind === "object_backed" && (
        <>
          <FormGroup label="Mid ObjectType">
            <HTMLSelect fill value={value.midObjectType} onChange={(e) => onChange({ midObjectType: e.target.value })}>
              <option value="">Select…</option>
              {objectTypeNames.map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </HTMLSelect>
          </FormGroup>
          <FormGroup label="Mid → source property">
            <InputGroup
              value={value.midSourceProperty}
              onChange={(e) => onChange({ midSourceProperty: e.target.value })}
            />
          </FormGroup>
          <FormGroup label="Mid → target property">
            <InputGroup
              value={value.midTargetProperty}
              onChange={(e) => onChange({ midTargetProperty: e.target.value })}
            />
          </FormGroup>
        </>
      )}
      <FormGroup label="Status">
        <HTMLSelect fill value={value.lifecycleStatus} onChange={(e) => onChange({ lifecycleStatus: e.target.value })}>
          {REGISTRY_LIFECYCLE_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </HTMLSelect>
      </FormGroup>
      {value.lifecycleStatus === "deprecated" && (
        <>
          <FormGroup label="Deprecation reason">
            <InputGroup
              value={value.deprecationReason}
              onChange={(e) => onChange({ deprecationReason: e.target.value })}
            />
          </FormGroup>
          <FormGroup label="Deprecation deadline">
            <InputGroup
              type="date"
              value={value.deprecationDeadline}
              onChange={(e) => onChange({ deprecationDeadline: e.target.value })}
            />
          </FormGroup>
          <FormGroup label="Replacement URN">
            <InputGroup
              className="hl-mono"
              value={value.replacementUrn}
              onChange={(e) => onChange({ replacementUrn: e.target.value })}
            />
          </FormGroup>
        </>
      )}
      <FormGroup label="Type classes (comma-separated)" helperText="e.g. hierarchy:parent">
        <InputGroup
          value={value.typeClasses}
          onChange={(e) => onChange({ typeClasses: e.target.value })}
          placeholder="hierarchy:parent"
        />
      </FormGroup>
      <FormGroup label="Source display name">
        <InputGroup
          value={value.sourceDisplayName}
          onChange={(e) => onChange({ sourceDisplayName: e.target.value })}
        />
      </FormGroup>
      <FormGroup label="Source plural display name">
        <InputGroup
          value={value.sourcePluralDisplayName}
          onChange={(e) => onChange({ sourcePluralDisplayName: e.target.value })}
        />
      </FormGroup>
      <FormGroup label="Source API name">
        <InputGroup value={value.sourceApiName} onChange={(e) => onChange({ sourceApiName: e.target.value })} />
      </FormGroup>
      <FormGroup label="Source visibility">
        <HTMLSelect
          fill
          value={value.sourceVisibility}
          onChange={(e) => onChange({ sourceVisibility: e.target.value })}
        >
          {VISIBILITIES.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </HTMLSelect>
      </FormGroup>
      <FormGroup label="Target display name">
        <InputGroup
          value={value.targetDisplayName}
          onChange={(e) => onChange({ targetDisplayName: e.target.value })}
        />
      </FormGroup>
      <FormGroup label="Target plural display name">
        <InputGroup
          value={value.targetPluralDisplayName}
          onChange={(e) => onChange({ targetPluralDisplayName: e.target.value })}
        />
      </FormGroup>
      <FormGroup label="Target API name">
        <InputGroup value={value.targetApiName} onChange={(e) => onChange({ targetApiName: e.target.value })} />
      </FormGroup>
      <FormGroup label="Target visibility">
        <HTMLSelect
          fill
          value={value.targetVisibility}
          onChange={(e) => onChange({ targetVisibility: e.target.value })}
        >
          {VISIBILITIES.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </HTMLSelect>
      </FormGroup>
      <FormGroup label="Project (optional)">
        <HTMLSelect fill value={value.projectUrn} onChange={(e) => onChange({ projectUrn: e.target.value })}>
          <option value="">Workspace only</option>
          {projects.map((p) => (
            <option key={p.urn} value={p.urn}>
              {p.name}
            </option>
          ))}
        </HTMLSelect>
      </FormGroup>
    </>
  );
}
