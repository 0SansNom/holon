import { Checkbox, FormGroup, HTMLSelect, InputGroup, TextArea } from "@blueprintjs/core";
import { SECRET_REF_HELP } from "./shared";

type ScalarKind = "text" | "url" | "secret" | "number";

export type ConnectionField =
  | {
      kind: ScalarKind;
      id: string;
      label: string;
      value: string;
      onChange: (value: string) => void;
      placeholder?: string;
      helperText?: string;
      disabled?: boolean;
    }
  | {
      kind: "secretRef";
      id: string;
      value: string;
      onChange: (value: string) => void;
      placeholder?: string;
    }
  | {
      kind: "select";
      id: string;
      label: string;
      value: string;
      onChange: (value: string) => void;
      options: { value: string; label: string }[];
      helperText?: string;
      disabled?: boolean;
      /** SQL dialect stretches; the object-storage kind select stays content-sized. */
      fill?: boolean;
    }
  | {
      kind: "textarea";
      id: string;
      label: string;
      value: string;
      onChange: (value: string) => void;
      placeholder?: string;
      helperText?: string;
      rows?: number;
      mono?: boolean;
    }
  | {
      kind: "checkbox";
      id: string;
      label: string;
      checked: boolean;
      onChange: (checked: boolean) => void;
      helperText?: string;
    };

function scalarInputType(kind: ScalarKind): "text" | "url" | "password" | "number" {
  if (kind === "secret") return "password";
  if (kind === "number") return "number";
  if (kind === "url") return "url";
  return "text";
}

export function ConnectionFields({ fields }: { fields: ConnectionField[] }) {
  return (
    <>
      {fields.map((field) => {
        if (field.kind === "secretRef") {
          return (
            <SecretRefField
              key={field.id}
              id={field.id}
              value={field.value}
              onChange={field.onChange}
              placeholder={field.placeholder}
            />
          );
        }
        if (field.kind === "select") {
          return (
            <FormGroup key={field.id} label={field.label} labelFor={field.id} helperText={field.helperText}>
              <HTMLSelect
                id={field.id}
                fill={field.fill ?? true}
                value={field.value}
                disabled={field.disabled}
                onChange={(event) => field.onChange(event.target.value)}
              >
                {field.options.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </HTMLSelect>
            </FormGroup>
          );
        }
        if (field.kind === "textarea") {
          return (
            <FormGroup key={field.id} label={field.label} labelFor={field.id} helperText={field.helperText}>
              <TextArea
                id={field.id}
                fill
                rows={field.rows ?? 4}
                value={field.value}
                placeholder={field.placeholder}
                onChange={(event) => field.onChange(event.target.value)}
                style={field.mono ? { fontFamily: "var(--font-mono, monospace)", fontSize: 12 } : undefined}
              />
            </FormGroup>
          );
        }
        if (field.kind === "checkbox") {
          return (
            <FormGroup key={field.id}>
              <Checkbox
                id={field.id}
                checked={field.checked}
                label={field.label}
                onChange={(event) => field.onChange((event.target as HTMLInputElement).checked)}
              />
              {field.helperText ? <p className="hl-text-muted-sm hl-mt-xs">{field.helperText}</p> : null}
            </FormGroup>
          );
        }
        return (
          <FormGroup key={field.id} label={field.label} labelFor={field.id} helperText={field.helperText}>
            <InputGroup
              id={field.id}
              type={scalarInputType(field.kind)}
              value={field.value}
              placeholder={field.placeholder}
              disabled={field.disabled}
              onChange={(event) => field.onChange(event.target.value)}
            />
          </FormGroup>
        );
      })}
    </>
  );
}

export function SecretRefField({
  id,
  value,
  onChange,
  placeholder = "env:ERP_PASSWORD",
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}) {
  return (
    <FormGroup label="Secret reference" labelFor={id} helperText={SECRET_REF_HELP}>
      <InputGroup id={id} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />
    </FormGroup>
  );
}
