"""Identity and authorization fixture.

The principal set here exercises both PDP engines independently:
jdoe is granted by ReBAC and passes ABAC; alice is denied by ReBAC alone
(tenant member, no workspace grant); kenji is granted by ReBAC but denied
by ABAC (workspace viewer, non-EU country). msmith is a workspace `admin` —
the principal who can approve high-risk Actions — while jdoe stays `editor`-only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import asyncpg

from holon_common import Principal, build_urn
from holon_common.authz import PermissionClient

_DDL = """
CREATE TABLE IF NOT EXISTS tenant (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS workspace (
    workspace_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenant(tenant_id),
    display_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS principal (
    urn TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    on_behalf_of TEXT,
    country TEXT,
    client_secret TEXT NOT NULL DEFAULT 'unset',
    status TEXT NOT NULL DEFAULT 'active',
    oidc_sub TEXT
);

-- additive migrations for databases seeded before these columns existed
ALTER TABLE principal ADD COLUMN IF NOT EXISTS country TEXT;
ALTER TABLE principal ADD COLUMN IF NOT EXISTS client_secret TEXT NOT NULL DEFAULT 'unset';
ALTER TABLE principal ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE principal ADD COLUMN IF NOT EXISTS oidc_sub TEXT;
ALTER TABLE tenant ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE workspace ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';

-- Org/Space/Project hierarchy — one tier below Workspace,
-- created at runtime via governance (unlike tenant/workspace, which are
-- fixed at bootstrap in this build).
CREATE TABLE IF NOT EXISTS project (
    urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS principal_oidc_sub_uidx
    ON principal (oidc_sub) WHERE oidc_sub IS NOT NULL;

-- OIDC authorization-code + PKCE state, keyed by the `state` param —
-- Postgres instead of in-process memory so `/oidc/login` and
-- `/oidc/callback` can land on different `identity` replicas (ADR 026,
-- multi-replica follow-up). Short-lived (10 min, see oidc.py's cleanup
-- query) and single-use (deleted by exchange_code on first read).
CREATE TABLE IF NOT EXISTS oidc_pending_state (
    state TEXT PRIMARY KEY,
    verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def client_secret_for(local_name: str) -> str:
    """Deterministic dev-only credential — `POST /token` requires this to
    verify credentials.
    """
    return f"{local_name}-dev-secret"

# Principals granted direct workspace access. alice is
# intentionally absent — a tenant member with no workspace relation.
WORKSPACE_VIEWERS = [("user", "jdoe"), ("user", "kenji"), ("agent", "ingest-bot")]

# `write` requires `editor`, a strictly smaller set than `viewer` (kenji can
# read but not invoke Actions — proves read and write are separate grants).
WORKSPACE_EDITORS = [("user", "jdoe"), ("service-account", "connectivity-connector")]

# `approve` requires `admin`, a strictly smaller set than `editor` — jdoe is
# deliberately absent here (separation of duties).
WORKSPACE_ADMINS = [("user", "msmith")]


def tenant_urn(tenant_id: str) -> str:
    return build_urn(tenant_id, "global", "tenant", tenant_id)


def workspace_urn(tenant_id: str, workspace_id: str) -> str:
    return build_urn(tenant_id, "global", "workspace", workspace_id)


def project_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    return build_urn(tenant_id, workspace_id, "project", name)


VALID_WORKSPACE_RELATIONS = {"viewer", "editor", "admin"}
VALID_PROJECT_RELATIONS = {"viewer", "editor", "admin"}


def seed_principals(tenant_id: str) -> list[Principal]:
    return [
        Principal(
            urn=build_urn(tenant_id, "global", "user", "jdoe"),
            type="user",
            tenant_id=tenant_id,
            display_name="Jane Doe",
            country="FR",
        ),
        Principal(
            urn=build_urn(tenant_id, "global", "user", "msmith"),
            type="user",
            tenant_id=tenant_id,
            display_name="Mary Smith",
            country="DE",
        ),
        Principal(
            urn=build_urn(tenant_id, "global", "user", "kenji"),
            type="user",
            tenant_id=tenant_id,
            display_name="Kenji Sato",
            country="JP",
        ),
        Principal(
            urn=build_urn(tenant_id, "global", "user", "alice"),
            type="user",
            tenant_id=tenant_id,
            display_name="Alice TenantMember",
            country="FR",
        ),
        Principal(
            urn=build_urn(tenant_id, "global", "agent", "ingest-bot"),
            type="agent",
            tenant_id=tenant_id,
            display_name="Ingest Bot",
            on_behalf_of=build_urn(tenant_id, "global", "user", "jdoe"),
            country="FR",
        ),
        Principal(
            urn=build_urn(tenant_id, "global", "service-account", "connectivity-connector"),
            type="service_account",
            tenant_id=tenant_id,
            display_name="Connectivity Connector",
        ),
    ]


async def ensure_seeded(conn: asyncpg.Connection, tenant_id: str, workspace_id: str) -> None:
    await conn.execute(_DDL)
    await conn.execute(
        "INSERT INTO tenant (tenant_id, display_name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        tenant_id,
        "Acme Corp",
    )
    await conn.execute(
        "INSERT INTO workspace (workspace_id, tenant_id, display_name) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        workspace_id,
        tenant_id,
        "Default Workspace",
    )

    for principal in seed_principals(tenant_id):
        local_name = principal.urn.split(":")[-1]
        await conn.execute(
            """
            INSERT INTO principal (urn, type, tenant_id, display_name, on_behalf_of, country, client_secret)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (urn) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                on_behalf_of = EXCLUDED.on_behalf_of,
                country = EXCLUDED.country,
                client_secret = EXCLUDED.client_secret
            """,
            principal.urn,
            principal.type,
            principal.tenant_id,
            principal.display_name,
            principal.on_behalf_of,
            principal.country,
            client_secret_for(local_name),
        )


async def create_project(pool: asyncpg.Pool, *, tenant_id: str, workspace_id: str, name: str) -> dict:
    urn = project_urn(tenant_id, workspace_id, name)
    await pool.execute(
        "INSERT INTO project (urn, tenant_id, workspace_id, name) VALUES ($1, $2, $3, $4)",
        urn, tenant_id, workspace_id, name,
    )
    return await get_project(pool, urn)


async def get_project(pool: asyncpg.Pool, urn: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM project WHERE urn = $1", urn)
    return dict(row) if row else None


async def list_projects(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM project WHERE tenant_id = $1 ORDER BY name", tenant_id)
    return [dict(row) for row in rows]


async def create_tenant(pool: asyncpg.Pool, *, tenant_id: str, display_name: str) -> dict:
    await pool.execute(
        """
        INSERT INTO tenant (tenant_id, display_name, status)
        VALUES ($1, $2, 'active')
        """,
        tenant_id,
        display_name,
    )
    return await get_tenant(pool, tenant_id)


async def get_tenant(pool: asyncpg.Pool, tenant_id: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM tenant WHERE tenant_id = $1", tenant_id)
    return dict(row) if row else None


async def list_tenants(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch("SELECT * FROM tenant ORDER BY tenant_id")
    return [dict(row) for row in rows]


async def set_tenant_status(pool: asyncpg.Pool, tenant_id: str, status: str) -> Optional[dict]:
    await pool.execute("UPDATE tenant SET status = $2 WHERE tenant_id = $1", tenant_id, status)
    return await get_tenant(pool, tenant_id)


async def create_workspace(
    pool: asyncpg.Pool, *, tenant_id: str, workspace_id: str, display_name: str
) -> dict:
    await pool.execute(
        """
        INSERT INTO workspace (workspace_id, tenant_id, display_name, status)
        VALUES ($1, $2, $3, 'active')
        """,
        workspace_id,
        tenant_id,
        display_name,
    )
    return await get_workspace(pool, workspace_id)


async def get_workspace(pool: asyncpg.Pool, workspace_id: str) -> Optional[dict]:
    row = await pool.fetchrow("SELECT * FROM workspace WHERE workspace_id = $1", workspace_id)
    return dict(row) if row else None


async def list_workspaces(pool: asyncpg.Pool, tenant_id: Optional[str] = None) -> list[dict]:
    if tenant_id is None:
        rows = await pool.fetch("SELECT * FROM workspace ORDER BY tenant_id, workspace_id")
    else:
        rows = await pool.fetch(
            "SELECT * FROM workspace WHERE tenant_id = $1 ORDER BY workspace_id", tenant_id
        )
    return [dict(row) for row in rows]


async def set_workspace_status(pool: asyncpg.Pool, workspace_id: str, status: str) -> Optional[dict]:
    await pool.execute(
        "UPDATE workspace SET status = $2 WHERE workspace_id = $1", workspace_id, status
    )
    return await get_workspace(pool, workspace_id)


async def insert_principal(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    type: str,
    local_name: str,
    display_name: str,
    country: Optional[str] = None,
    on_behalf_of: Optional[str] = None,
    client_secret: Optional[str] = None,
    oidc_sub: Optional[str] = None,
) -> dict:
    type_segment = {"service_account": "service-account", "service-account": "service-account"}.get(type, type)
    urn = build_urn(tenant_id, "global", type_segment, local_name)
    secret = client_secret if client_secret is not None else client_secret_for(local_name)
    db_type = "service_account" if type in ("service_account", "service-account") else type
    await pool.execute(
        """
        INSERT INTO principal (urn, type, tenant_id, display_name, on_behalf_of, country, client_secret, status, oidc_sub)
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'active', $8)
        """,
        urn,
        db_type,
        tenant_id,
        display_name,
        on_behalf_of,
        country,
        secret,
        oidc_sub,
    )
    row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    return dict(row)


async def set_principal_status(pool: asyncpg.Pool, urn: str, status: str) -> Optional[dict]:
    await pool.execute("UPDATE principal SET status = $2 WHERE urn = $1", urn, status)
    row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    return dict(row) if row else None


async def ensure_authz_seeded(client: PermissionClient, schema_path: str, tenant_id: str, workspace_id: str) -> None:
    """Loads the shared SpiceDB schema and writes the tenant/workspace
    relationships Identity owns. Knowledge separately links its own
    ObjectType resources under the same workspace when it seeds its ontology.
    """
    await client.write_schema(Path(schema_path).read_text())

    t_urn = tenant_urn(tenant_id)
    w_urn = workspace_urn(tenant_id, workspace_id)

    for principal in seed_principals(tenant_id):
        await client.write_relationship(
            resource_type="tenant", resource_urn=t_urn, relation="member", subject_urn=principal.urn
        )

    for subject_type, local_id in WORKSPACE_VIEWERS:
        await client.write_relationship(
            resource_type="workspace",
            resource_urn=w_urn,
            relation="viewer",
            subject_urn=build_urn(tenant_id, "global", subject_type, local_id),
        )

    for subject_type, local_id in WORKSPACE_EDITORS:
        await client.write_relationship(
            resource_type="workspace",
            resource_urn=w_urn,
            relation="editor",
            subject_urn=build_urn(tenant_id, "global", subject_type, local_id),
        )

    for subject_type, local_id in WORKSPACE_ADMINS:
        await client.write_relationship(
            resource_type="workspace",
            resource_urn=w_urn,
            relation="admin",
            subject_urn=build_urn(tenant_id, "global", subject_type, local_id),
        )
