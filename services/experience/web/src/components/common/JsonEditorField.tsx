import Editor from "@monaco-editor/react";
import { FormGroup, type FormGroupProps } from "@blueprintjs/core";
import { useMonacoEditorTheme } from "../../hooks/useMonacoEditorTheme";

export function JsonEditorField({
  label,
  helperText,
  value,
  onChange,
  height = 90,
  ...formGroupProps
}: {
  label: string;
  helperText?: FormGroupProps["helperText"];
  value: string;
  onChange: (value: string) => void;
  height?: number;
} & Omit<FormGroupProps, "label" | "helperText" | "children" | "onChange">) {
  const monacoTheme = useMonacoEditorTheme();
  return (
    <FormGroup label={label} helperText={helperText} {...formGroupProps}>
      <div className="hl-json-editor">
        <Editor
          height={`${height}px`}
          defaultLanguage="json"
          theme={monacoTheme}
          value={value}
          onChange={(v) => onChange(v ?? "")}
          options={{ minimap: { enabled: false }, fontSize: 12 }}
        />
      </div>
    </FormGroup>
  );
}
