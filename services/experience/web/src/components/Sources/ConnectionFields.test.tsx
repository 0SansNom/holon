import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ConnectionFields } from "./ConnectionFields";

function Harness() {
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  return (
    <ConnectionFields
      fields={[
        { kind: "url", id: "endpoint", label: "Endpoint", value: url, onChange: setUrl, placeholder: "https://example" },
        { kind: "secretRef", id: "secret-ref", value: secret, onChange: setSecret, placeholder: "env:ERP_PASSWORD" },
      ]}
    />
  );
}

function ConstrainedHarness() {
  const [name, setName] = useState("erp");
  const [dialect, setDialect] = useState("postgres");
  const [password, setPassword] = useState("");
  return (
    <ConnectionFields
      fields={[
        { kind: "text", id: "name", label: "Name", value: name, onChange: setName, disabled: true },
        {
          kind: "select",
          id: "dialect",
          label: "Dialect",
          value: dialect,
          onChange: setDialect,
          helperText: "Wire protocol family",
          disabled: true,
          options: [{ value: "postgres", label: "PostgreSQL" }],
        },
        { kind: "secret", id: "password", label: "Password", value: password, onChange: setPassword },
      ]}
    />
  );
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

  it("disables a field, shows select help, and masks a secret", () => {
    render(<ConstrainedHarness />);

    expect(screen.getByLabelText("Name")).toBeDisabled();
    const dialect = screen.getByLabelText("Dialect");
    expect(dialect).toBeDisabled();
    expect(screen.getByText("Wire protocol family")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
  });
});
