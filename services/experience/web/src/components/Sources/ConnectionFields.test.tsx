import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ConnectionFields, type ConnectionField } from "./ConnectionFields";

function Harness() {
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [dialect, setDialect] = useState("postgres");
  const [notes, setNotes] = useState("");
  const [pathStyle, setPathStyle] = useState(true);
  const fields: ConnectionField[] = [
    { kind: "url", id: "endpoint", label: "Endpoint", value: url, onChange: setUrl, placeholder: "https://example" },
    { kind: "secretRef", id: "secret-ref", value: secret, onChange: setSecret, placeholder: "env:ERP_PASSWORD" },
    {
      kind: "select",
      id: "dialect",
      label: "Dialect",
      value: dialect,
      onChange: setDialect,
      options: [
        { value: "postgres", label: "PostgreSQL" },
        { value: "snowflake", label: "Snowflake" },
      ],
    },
    {
      kind: "text",
      id: "locked-name",
      label: "Name",
      value: "erp_prod",
      onChange: () => undefined,
      disabled: true,
    },
    {
      kind: "textarea",
      id: "notes",
      label: "Service account JSON",
      value: notes,
      onChange: setNotes,
      rows: 6,
      mono: true,
    },
    {
      kind: "checkbox",
      id: "path-style",
      label: "Path-style addressing",
      checked: pathStyle,
      onChange: setPathStyle,
      helperText: "Enable for MinIO",
    },
  ];
  return <ConnectionFields fields={fields} />;
}

describe("ConnectionFields", () => {
  it("renders a url field and a secret reference", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    const endpoint = screen.getByLabelText("Endpoint");
    expect(endpoint).toHaveAttribute("type", "url");
    await user.type(endpoint, "https://db.example");
    expect(endpoint).toHaveValue("https://db.example");

    const secret = screen.getByLabelText("Secret reference");
    expect(secret).toHaveAttribute("type", "text");
    expect(screen.getByText(/Holon stores the reference/)).toBeInTheDocument();
    await user.type(secret, "env:ERP_PASSWORD");
    expect(secret).toHaveValue("env:ERP_PASSWORD");
  });

  it("renders select, disabled text, textarea, and checkbox fields", async () => {
    const user = userEvent.setup();
    render(<Harness />);

    expect(screen.getByLabelText("Name")).toBeDisabled();

    const dialect = screen.getByLabelText("Dialect");
    await user.selectOptions(dialect, "snowflake");
    expect(dialect).toHaveValue("snowflake");

    const notes = screen.getByLabelText("Service account JSON");
    expect(notes.tagName).toBe("TEXTAREA");
    await user.type(notes, "sa");
    expect(notes).toHaveValue("sa");

    const pathStyle = screen.getByLabelText("Path-style addressing");
    expect(pathStyle).toBeChecked();
    expect(screen.getByText("Enable for MinIO")).toBeInTheDocument();
    await user.click(pathStyle);
    expect(pathStyle).not.toBeChecked();
  });
});
