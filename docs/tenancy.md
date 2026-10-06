# Tenancy

URNs (`hl:{tenant}:{workspace}:{type}:{id}`), Postgres rows, search
documents, SpiceDB tuples and audit records all carry a tenant. That is
the data model. The runtime is narrower: **one deployment serves one
tenant**. Run a separate deployment (Helm release or compose stack) per
subsidiary that needs its own tenant.

## What follows the caller

Requests are scoped by the caller's token, not by the process:

- `principal.tenant_id` scopes reads, writes, search and audit queries in
  every service.
- The workspace comes from `workspaceId` / `X-Holon-Workspace-Id` when the
  route accepts it (Knowledge, Connectivity), and falls back to the
  process workspace otherwise.
- Knowledge's boot authz seed walks every tenant and workspace present in
  its catalogue.

## What is bound to the process

Each process reads one `HOLON_TENANT_ID` / `HOLON_WORKSPACE_ID` pair
(Helm: `bootstrap.tenantId` / `bootstrap.workspaceId`). These use it:

- Service accounts and background actors: Connectivity's scheduler,
  stream ingest and pipeline runner, Automation's workflow engine,
  Intelligence's indexer, and the ingest agent in Experience and
  Intelligence.
- Experience: `GET /api/config`, the Application Builder, and every call
  it makes to Knowledge (`/api/ontologies/{HOLON_WORKSPACE_ID}/…`).
- Revocation snapshot hydration (`holon_common.principal_status`).
- The default workspace of any route that does not take one.

A second tenant in the same deployment can sign in and keep its data
apart, but its syncs, workflows, indexing and UI would run as, or
against, the process tenant. That is not supported.

## Several workspaces in one tenant

Knowledge and Connectivity routes accept an explicit workspace, so API
clients can work across workspaces of one tenant. Experience and the
background actors above still use the process workspace.
