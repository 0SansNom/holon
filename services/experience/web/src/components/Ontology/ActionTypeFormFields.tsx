import { FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import type { InterfaceType, ObjectType } from "../../api/knowledge";
import { JsonEditorField } from "../common/JsonEditorField";
import { RISK_LEVELS, type ActionTypeFormState } from "./actionTypeForm";

export function ActionTypeFormFields({
  mode,
  value,
  onChange,
  objectTypes,
  interfaces,
  fixedName,
}: {
  mode: "create" | "edit";
  value: ActionTypeFormState;
  onChange: (patch: Partial<ActionTypeFormState>) => void;
  objectTypes: ObjectType[];
  interfaces: InterfaceType[];
  fixedName?: string;
}) {
  return (
    <>
      {mode === "create" ? (
        <>
          <div className="hl-flex-row hl-gap-sm">
            <FormGroup label="Target" className="hl-flex-1">
              <HTMLSelect
                fill
                value={value.targetKind}
                onChange={(e) => onChange({ targetKind: e.target.value as "object_type" | "interface" })}
              >
                <option value="object_type">ObjectType</option>
                <option value="interface">Interface</option>
              </HTMLSelect>
            </FormGroup>
            <FormGroup
              label={value.targetKind === "object_type" ? "Target ObjectType" : "Target Interface"}
              className="hl-flex-1"
              helperText={
                value.targetKind === "interface"
                  ? "Invocable on any ObjectType implementing it; edits limited to its required_properties."
                  : undefined
              }
            >
              {value.targetKind === "object_type" ? (
                <HTMLSelect fill value={value.targetObjectType} onChange={(e) => onChange({ targetObjectType: e.target.value })}>
                  <option value="">Select…</option>
                  {objectTypes.map((ot) => (
                    <option key={ot.name} value={ot.name}>
                      {ot.name}
                    </option>
                  ))}
                </HTMLSelect>
              ) : (
                <HTMLSelect fill value={value.targetInterface} onChange={(e) => onChange({ targetInterface: e.target.value })}>
                  <option value="">Select…</option>
                  {interfaces.map((iface) => (
                    <option key={iface.name} value={iface.name}>
                      {iface.name}
                    </option>
                  ))}
                </HTMLSelect>
              )}
            </FormGroup>
          </div>
          <FormGroup label="Local name">
            <InputGroup placeholder="setPriority" value={value.localName} onChange={(e) => onChange({ localName: e.target.value })} />
          </FormGroup>
        </>
      ) : (
        <p className="hl-text-muted">
          Name and target (<Tag minimal>{fixedName?.split(".")[0]}</Tag>) aren't editable — everything else is.
        </p>
      )}

      <div className="hl-flex-row hl-gap-sm">
        <FormGroup label="Required permission" className="hl-flex-1">
          <InputGroup value={value.requiredPermission} onChange={(e) => onChange({ requiredPermission: e.target.value })} />
        </FormGroup>
        <FormGroup label="Risk level" className="hl-flex-1">
          <HTMLSelect fill value={value.riskLevel} onChange={(e) => onChange({ riskLevel: e.target.value })} options={[...RISK_LEVELS]} />
        </FormGroup>
      </div>
      <FormGroup label="Description">
        <InputGroup
          placeholder={mode === "create" ? "What invoking this Action does" : undefined}
          value={value.description}
          onChange={(e) => onChange({ description: e.target.value })}
        />
      </FormGroup>

      <JsonEditorField
        label="Parameters"
        helperText={
          <span className="hl-mono">
            [{"{"}name, required, value_type{"}"} | {"{"}name, required, kind: "object_reference", object_type{"}"}, ...]
          </span>
        }
        value={value.parametersJson}
        onChange={(parametersJson) => onChange({ parametersJson })}
      />

      <FormGroup label="Edits kind">
        <HTMLSelect fill value={value.editsKind} onChange={(e) => onChange({ editsKind: e.target.value as "declarative" | "function" })}>
          <option value="declarative">Declarative (fixed edits)</option>
          <option value="function">Function-backed (a plugin computes the edits)</option>
        </HTMLSelect>
      </FormGroup>

      {value.editsKind === "declarative" ? (
        <JsonEditorField
          label="Edits"
          helperText={<span className="hl-mono">[{"{"}property, source, value|parameter_name{"}"}, ...]</span>}
          value={value.editsJson}
          onChange={(editsJson) => onChange({ editsJson })}
        />
      ) : (
        <FormGroup label="Edit function" helperText="Name of a registered, active Function plugin — its return value becomes the applied edits.">
          <InputGroup
            placeholder="customer_value_model_function"
            value={value.editFunctionName}
            onChange={(e) => onChange({ editFunctionName: e.target.value })}
          />
        </FormGroup>
      )}

      <JsonEditorField
        label="Submission criteria"
        helperText={<span className="hl-mono">[{"{"}property, operator, value{"}"}, ...]</span>}
        value={value.criteriaJson}
        onChange={(criteriaJson) => onChange({ criteriaJson })}
        height={70}
      />

      <JsonEditorField
        label="Sections"
        helperText={
          <span className="hl-mono">
            [{"{"}name, parameter_names{"}"}, ...] — groups parameters in the invocation form; a parameter not listed renders ungrouped.
          </span>
        }
        value={value.sectionsJson}
        onChange={(sectionsJson) => onChange({ sectionsJson })}
        height={70}
      />
    </>
  );
}
