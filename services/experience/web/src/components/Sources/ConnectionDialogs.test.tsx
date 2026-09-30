import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ConnectionDialog } from "./ConnectionDialog";
import { ObjectConnectionDialog } from "./ObjectConnectionDialog";
import { SalesforceConnectionDialog } from "./SalesforceConnectionDialog";
import { SftpConnectionDialog } from "./SftpConnectionDialog";
import { SqlConnectionDialog } from "./SqlConnectionDialog";

const hooks = vi.hoisted(() => ({ requireSecretRef: false }));

vi.mock("../../api/hooks", () => ({
  useBootstrapConfig: () => ({ data: { require_connector_secret_ref: hooks.requireSecretRef } }),
  useRegisterConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRegisterSqlConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRegisterObjectConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRegisterSftpConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRegisterSalesforceConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

describe("connection dialogs", () => {
  beforeEach(() => {
    hooks.requireSecretRef = false;
  });

  it("renders every SFTP field, and hides the password when a secret reference is required", () => {
    const { unmount } = render(<SftpConnectionDialog editing={null} onClose={() => undefined} />);
    expect(screen.getByLabelText("Name")).toBeEnabled();
    expect(screen.getByLabelText("Host")).toBeInTheDocument();
    expect(screen.getByLabelText("Port")).toHaveAttribute("type", "number");
    expect(screen.getByLabelText("Username")).toBeInTheDocument();
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Secret reference")).toBeInTheDocument();
    unmount();

    hooks.requireSecretRef = true;
    render(<SftpConnectionDialog editing={null} onClose={() => undefined} />);
    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Secret reference")).toBeInTheDocument();
  });

  it("locks the SQL name and dialect while editing, and keeps the password blank", () => {
    render(
      <SqlConnectionDialog
        editing={{
          tenant_id: "acme",
          name: "erp",
          dialect: "postgres",
          host: "db.example",
          port: 5432,
          database: "analytics",
          username: "readonly",
          has_password: true,
          created_by_urn: "hl:acme:global:user:admin",
          created_at: "2026-01-01T00:00:00Z",
        }}
        onClose={() => undefined}
      />,
    );

    expect(screen.getByLabelText("Name")).toBeDisabled();
    expect(screen.getByLabelText("Dialect")).toBeDisabled();
    expect(screen.queryByLabelText("Warehouse")).not.toBeInTheDocument();
    expect(screen.getByPlaceholderText("•••••••• (unchanged)")).toBeInTheDocument();
  });

  it("reveals the Snowflake warehouse and switches object-storage fields with the kind", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<SqlConnectionDialog editing={null} onClose={() => undefined} />);
    await user.selectOptions(screen.getByLabelText("Dialect"), "snowflake");
    expect(screen.getByLabelText("Account / host")).toBeInTheDocument();
    expect(screen.getByLabelText("Warehouse")).toBeInTheDocument();
    expect(screen.getByLabelText("Port")).toHaveValue(443);
    unmount();

    render(<ObjectConnectionDialog editing={null} onClose={() => undefined} />);
    expect(screen.getByLabelText("Access key ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Path-style addressing")).toBeChecked();
    await user.selectOptions(screen.getByLabelText("Kind"), "gcs");
    expect(screen.getByLabelText("Project ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Service account JSON").tagName).toBe("TEXTAREA");
    expect(screen.queryByLabelText("Path-style addressing")).not.toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Kind"), "azure");
    expect(screen.getByLabelText("Storage account name")).toBeInTheDocument();
    expect(screen.getByLabelText("Account key")).toHaveAttribute("type", "password");
  });

  it("renders REST and Salesforce credentials through the shared field list", () => {
    const { unmount } = render(<ConnectionDialog editing={null} onClose={() => undefined} />);
    expect(screen.getByLabelText("Allowed origin")).toBeInTheDocument();
    expect(screen.getByLabelText("Auth header name")).toBeInTheDocument();
    expect(screen.getByLabelText("Auth header value")).toHaveAttribute("type", "password");
    unmount();

    render(<SalesforceConnectionDialog editing={null} onClose={() => undefined} />);
    expect(screen.getByLabelText("Login URL")).toBeInTheDocument();
    expect(screen.getByLabelText("Consumer Key (Client ID)")).toBeInTheDocument();
    expect(screen.getByLabelText("Consumer Secret")).toHaveAttribute("type", "password");
    expect(screen.getByLabelText("Secret reference")).toBeInTheDocument();
  });
});