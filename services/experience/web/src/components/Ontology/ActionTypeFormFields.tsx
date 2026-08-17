import { FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import type { InterfaceType, ObjectType } from "../../api/knowledge";
import { JsonEditorField } from "../common/JsonEditorField";
import { RISK_LEVELS, type ActionTypeFormState } from "./actionTypeForm";
import { useTypeClasses } from "../../api/hooks";

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
  const { data: typeClasses } = useTypeClasses();
  const actionTypeClassSuggestions = (typeClasses ?? [])
    .filter((c) => c.id.startsWith("hubble-oe:") || c.id.startsWith("actions:"))
    .map((c) => c.id);

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

      <FormGroup
        label="Type classes"
        helperText={
          actionTypeClassSuggestions.length > 0
            ? `Comma-separated Foundry kind:name — e.g. ${actionTypeClassSuggestions.join(", ")}`
            : "Comma-separated Foundry kind:name (e.g. hubble-oe:hide-action)"
        }
      >
        <InputGroup
          placeholder="hubble-oe:hide-action"
          value={value.typeClasses}
          onChange={(e) => onChange({ typeClasses: e.target.value })}
        />
      </FormGroup>

      <FormGroup label="Status">
        <HTMLSelect
          fill
          value={value.lifecycleStatus}
          onChange={(e) => onChange({ lifecycleStatus: e.target.value })}
        >
          <option value="experimental">experimental</option>
          <option value="active">active</option>
          <option value="deprecated">deprecated</option>
          <option value="example">example</option>
        </HTMLSelect>
      </FormGroup>
      {value.lifecycleStatus === "deprecated" && (
        <>
          <FormGroup label="Deprecation reason" helperText="Required when deprecated">
            <InputGroup
              value={value.deprecationReason}
              onChange={(e) => onChange({ deprecationReason: e.target.value })}
            />
          </FormGroup>
          <FormGroup label="Deprecation deadline" helperText="Required when deprecated">
            <InputGroup
              type="date"
              value={value.deprecationDeadline}
              onChange={(e) => onChange({ deprecationDeadline: e.target.value })}
            />
          </FormGroup>
          <FormGroup label="Replacement URN (optional)">
            <InputGroup
              className="hl-mono"
              value={value.replacementUrn}
              onChange={(e) => onChange({ replacementUrn: e.target.value })}
              placeholder="hl:…:action-type:…"
            />
          </FormGroup>
        </>
      )}

      <JsonEditorField
        label="Parameters"
        helperText={
          <span>
            Optional <span className="hl-mono">default</span>:{" "}
            <span className="hl-mono">{"{kind:'static',value}"}</span>,{" "}
            <span className="hl-mono">{"{kind:'current_object'}"}</span>, or{" "}
            <span className="hl-mono">{"{kind:'object_property',object:'current'|param,property}"}</span>
            . object_reference may set <span className="hl-mono">object_set</span>. Type classes{" "}
            <span className="hl-mono">actions:generate_uuid</span> /{" "}
            <span className="hl-mono">actions:prefill_current_user</span> still apply.
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
          helperText={
            <span>
              Rules JSON. Default kind <span className="hl-mono">modify_property</span>
              {" "}({"{" + "property, source, value|parameter_name}"}). Also{" "}
              <span className="hl-mono">create_link</span>/<span className="hl-mono">delete_link</span>
              {" "}(relation_type + ends), <span className="hl-mono">create_object</span>,{" "}
              <span className="hl-mono">delete_object</span>.
            </span>
          }
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
        helperText={
          <span>
            Flat AND list, or nested <span className="hl-mono">all</span>/<span className="hl-mono">any</span>.
            Leaf: property ops, or <span className="hl-mono">{"{principal:'urn'|'type',operator,value}"}</span>.
            Optional <span className="hl-mono">message</span>.
          </span>
        }
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

      <FormGroup
        label="Function side effect"
        helperText="Optional post-commit Function plugin name (runs after the Action applies)."
      >
        <InputGroup
          className="hl-mono"
          placeholder="notify_ops_channel"
          value={value.functionSideEffect ?? ""}
          onChange={(e) => onChange({ functionSideEffect: e.target.value })}
        />
      </FormGroup>

      <FormGroup
        label="Writeback dataset"
        helperText="Connectivity write_target name. Requires risk level high (Automation saga)."
      >
        <InputGroup
          className="hl-mono"
          placeholder="customers"
          value={value.writebackDataset ?? ""}
          onChange={(e) => onChange({ writebackDataset: e.target.value })}
          intent={value.writebackDataset && value.riskLevel !== "high" ? "warning" : undefined}
        />
      </FormGroup>

      <FormGroup
        label="Notify webhook"
        helperText="Optional HTTP(S) URL — best-effort POST after apply (Slack incoming webhook, Zapier, …). Env fallback: HOLON_ACTION_NOTIFY_WEBHOOK."
      >
        <InputGroup
          className="hl-mono"
          placeholder="https://hooks.slack.com/services/…"
          value={value.notifyWebhook ?? ""}
          onChange={(e) => onChange({ notifyWebhook: e.target.value })}
        />
      </FormGroup>
    </>
  );
}
