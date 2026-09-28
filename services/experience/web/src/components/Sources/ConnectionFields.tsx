import { FormGroup, HTMLSelect, InputGroup } from "@blueprintjs/core";
import { SECRET_REF_HELP } from "./shared";

export type ConnectionField =
  | {
      kind: "text" | "url" | "secret" | "number";
      id: string;
      label: string;
      value: string;
      onChange: (value: string) => void;
      placeholder?: string;
      helperText?: string;
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
    };

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
            <FormGroup key={field.id} label={field.label} labelFor={field.id}>
              <HTMLSelect
                id={field.id}
                fill
                value={field.value}
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
        return (
          <FormGroup key={field.id} label={field.label} labelFor={field.id} helperText={field.helperText}>
            <InputGroup
              id={field.id}
              type={field.kind === "secret" ? "password" : field.kind === "number" ? "number" : field.kind === "url" ? "url" : "text"}
              value={field.value}
              placeholder={field.placeholder}
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
