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
});
