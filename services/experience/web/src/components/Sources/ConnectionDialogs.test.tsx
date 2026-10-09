import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { GenericConnection, ObjectConnection, SalesforceConnection, SftpConnection, SqlConnection } from "../../api/connectivity";
import { ConnectionDialog } from "./ConnectionDialog";
import { ObjectConnectionDialog } from "./ObjectConnectionDialog";
import { SalesforceConnectionDialog } from "./SalesforceConnectionDialog";
import { SftpConnectionDialog } from "./SftpConnectionDialog";
import { SqlConnectionDialog } from "./SqlConnectionDialog";

const hooks = vi.hoisted(() => ({
  requireSecretRef: false,
  mutateAsync: vi.fn(),
}));

vi.mock("../../api/hooks", () => ({
  useBootstrapConfig: () => ({ data: { require_connector_secret_ref: hooks.requireSecretRef } }),
  useRegisterConnection: () => ({ mutateAsync: hooks.mutateAsync, isPending: false }),
  useRegisterSqlConnection: () => ({ mutateAsync: hooks.mutateAsync, isPending: false }),
  useRegisterObjectConnection: () => ({ mutateAsync: hooks.mutateAsync, isPending: false }),
  useRegisterSftpConnection: () => ({ mutateAsync: hooks.mutateAsync, isPending: false }),
  useRegisterSalesforceConnection: () => ({ mutateAsync: hooks.mutateAsync, isPending: false }),
}));

const sftpEditing: SftpConnection = {
  tenant_id: "acme",
  name: "erp_files",
  host: "sftp.example.com",
  port: 22,
  username: "readonly",
  has_password: true,
  created_by_urn: "hl:acme:global:user:ada",
  created_at: "2026-01-01T00:00:00Z",
};

const restEditing: GenericConnection = {
  tenant_id: "acme",
  name: "hubspot",
  auth_header_name: "Authorization",
  has_auth_header_value: true,
  allowed_origin: "https://api.hubapi.com",
  created_by_urn: "hl:acme:global:user:ada",
  created_at: "2026-01-01T00:00:00Z",
};

const sqlEditing: SqlConnection = {
  tenant_id: "acme",
  name: "erp",
  dialect: "postgres",
  host: "db.example.com",
  port: 5432,
  database: "analytics",
  username: "readonly_user",
  use_tls: false,
  has_password: true,
  created_by_urn: "hl:acme:global:user:ada",
  created_at: "2026-01-01T00:00:00Z",
};

const salesforceEditing: SalesforceConnection = {
  tenant_id: "acme",
  name: "sf_prod",
  login_url: "https://login.salesforce.com",
  client_id: "3MVG9",
  has_client_secret: true,
  instance_url: null,
  created_by_urn: "hl:acme:global:user:ada",
  created_at: "2026-01-01T00:00:00Z",
};

const objectEditing: ObjectConnection = {
  tenant_id: "acme",
  name: "bucket",
  kind: "s3",
  endpoint: "https://s3.amazonaws.com",
  region: "us-east-1",
  access_key_id: "AKIA",
  path_style: false,
  has_secret_access_key: true,
  created_by_urn: "hl:acme:global:user:ada",
  created_at: "2026-01-01T00:00:00Z",
};

describe("connection dialogs", () => {
  beforeEach(() => {
    hooks.requireSecretRef = false;
    hooks.mutateAsync.mockReset();
    hooks.mutateAsync.mockResolvedValue({});
  });

  it("opens an SFTP connection for creation and keeps the name fixed while editing", () => {
    const { unmount } = render(<SftpConnectionDialog editing={null} onClose={() => {}} />);
    expect(screen.getByRole("heading", { name: "New SFTP connection" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toBeEnabled();
    expect(screen.getByLabelText("Password")).toHaveAttribute("type", "password");
    unmount();

    render(<SftpConnectionDialog editing={sftpEditing} onClose={() => {}} />);
    expect(screen.getByRole("heading", { name: "Edit SFTP connection" })).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toBeDisabled();
    expect(screen.getByLabelText("Name")).toHaveValue("erp_files");
  });

  it("refuses to save an SFTP connection when a secret reference is required", async () => {
    hooks.requireSecretRef = true;
    const user = userEvent.setup();
    render(<SftpConnectionDialog editing={null} onClose={() => {}} />);

    expect(screen.queryByLabelText("Password")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Name"), "files");
    await user.type(screen.getByLabelText("Host"), "sftp.example.com");
    await user.type(screen.getByLabelText("Username"), "readonly");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();

    await user.type(screen.getByLabelText("Secret reference"), "env:HOLON_CONN_ACME__SFTP_PASSWORD");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  it("shows the API error in the SFTP callout", async () => {
    hooks.mutateAsync.mockRejectedValue(new ApiError(409, { detail: "connection name already exists" }));
    const user = userEvent.setup();
    render(<SftpConnectionDialog editing={null} onClose={() => {}} />);
    await user.type(screen.getByLabelText("Name"), "files");
    await user.type(screen.getByLabelText("Host"), "sftp.example.com");
    await user.type(screen.getByLabelText("Username"), "readonly");
    await user.type(screen.getByLabelText("Password"), "secret");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("connection name already exists")).toBeInTheDocument();
  });

  it("shows the API error in the REST callout", async () => {
    hooks.mutateAsync.mockRejectedValue(new ApiError(400, { detail: "origin not allowed" }));
    const user = userEvent.setup();
    render(<ConnectionDialog editing={restEditing} onClose={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("origin not allowed")).toBeInTheDocument();
  });

  it("keeps a REST connection name fixed and hides the header value when a secret reference is required", async () => {
    const { unmount } = render(<ConnectionDialog editing={restEditing} onClose={() => {}} />);
    expect(screen.getByLabelText("Name")).toBeDisabled();
    expect(screen.getByLabelText("Auth header value")).toBeInTheDocument();
    unmount();

    hooks.requireSecretRef = true;
    const user = userEvent.setup();
    render(<ConnectionDialog editing={null} onClose={() => {}} />);
    expect(screen.queryByLabelText("Auth header value")).not.toBeInTheDocument();
    await user.type(screen.getByLabelText("Name"), "hubspot");
    await user.type(screen.getByLabelText("Allowed origin"), "https://api.hubapi.com");
    await user.type(screen.getByLabelText("Auth header name"), "Authorization");
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("shows a Snowflake warehouse only for that dialect and resets a default port", async () => {
    const user = userEvent.setup();
    render(<SqlConnectionDialog editing={null} onClose={() => {}} />);
    expect(screen.queryByLabelText("Warehouse")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Port")).toHaveValue(5432);

    await user.selectOptions(screen.getByLabelText("Dialect"), "cockroachdb");
    expect(screen.getByLabelText("Port")).toHaveValue(26257);

    await user.selectOptions(screen.getByLabelText("Dialect"), "snowflake");
    expect(screen.getByLabelText("Warehouse")).toBeInTheDocument();
    expect(screen.getByLabelText("Account / host")).toBeInTheDocument();
    expect(screen.getByLabelText("Port")).toHaveValue(443);
  });

  it("defaults TLS from the dialect and hides it for Snowflake", async () => {
    const user = userEvent.setup();
    render(<SqlConnectionDialog editing={null} onClose={() => {}} />);
    expect(screen.getByLabelText("Require TLS")).not.toBeChecked();

    await user.selectOptions(screen.getByLabelText("Dialect"), "azure_synapse_serverless");
    expect(screen.getByLabelText("Require TLS")).toBeChecked();
    expect(screen.getByText(/Serverless allows OPENROWSET/)).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Dialect"), "snowflake");
    expect(screen.queryByLabelText("Require TLS")).not.toBeInTheDocument();
  });

  it("sends the TLS choice with the connection", async () => {
    const user = userEvent.setup();
    render(<SqlConnectionDialog editing={sqlEditing} onClose={() => {}} />);
    await user.click(screen.getByLabelText("Require TLS"));
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(hooks.mutateAsync).toHaveBeenCalledWith(expect.objectContaining({ dialect: "postgres", use_tls: true }));
  });

  it("shows the API error in the SQL callout", async () => {
    hooks.mutateAsync.mockRejectedValue(new ApiError(400, { detail: "warehouse rejected" }));
    const user = userEvent.setup();
    render(<SqlConnectionDialog editing={sqlEditing} onClose={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("warehouse rejected")).toBeInTheDocument();
  });

  it("keeps the SQL name and dialect fixed while editing", () => {
    render(<SqlConnectionDialog editing={sqlEditing} onClose={() => {}} />);
    expect(screen.getByLabelText("Name")).toBeDisabled();
    expect(screen.getByLabelText("Dialect")).toBeDisabled();
  });

  it("keeps the Salesforce name fixed and surfaces an API error", async () => {
    hooks.mutateAsync.mockRejectedValue(new ApiError(400, { detail: "client id rejected" }));
    const user = userEvent.setup();
    render(<SalesforceConnectionDialog editing={salesforceEditing} onClose={() => {}} />);
    expect(screen.getByLabelText("Name")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("client id rejected")).toBeInTheDocument();
  });

  it("switches object-storage fields with the kind and omits the inline secret when required", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<ObjectConnectionDialog editing={null} onClose={() => {}} />);
    expect(screen.getByLabelText("Access key ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Path-style addressing")).toBeChecked();
    expect(screen.getByLabelText("Secret access key")).toHaveAttribute("type", "password");

    await user.selectOptions(screen.getByLabelText("Kind"), "gcs");
    expect(screen.getByLabelText("Project ID")).toBeInTheDocument();
    expect(screen.getByLabelText("Service account JSON").tagName).toBe("TEXTAREA");
    expect(screen.queryByLabelText("Path-style addressing")).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Kind"), "azure");
    expect(screen.getByLabelText("Storage account name")).toBeInTheDocument();
    expect(screen.getByLabelText("Account key")).toHaveAttribute("type", "password");
    unmount();

    hooks.requireSecretRef = true;
    const edit = render(<ObjectConnectionDialog editing={objectEditing} onClose={() => {}} />);
    expect(screen.getByLabelText("Name")).toBeDisabled();
    expect(screen.getByLabelText("Kind")).toBeDisabled();
    expect(screen.queryByLabelText("Secret access key")).not.toBeInTheDocument();
    edit.unmount();

    hooks.mutateAsync.mockRejectedValue(new ApiError(400, { detail: "bucket rejected" }));
    render(<ObjectConnectionDialog editing={objectEditing} onClose={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("bucket rejected")).toBeInTheDocument();
  });
});
