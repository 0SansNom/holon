"""Identity database schema, tenant/workspace/project registry, and principal provisioning."""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from typing import Optional

import asyncpg

from holon_common import build_urn
from holon_common.spicedb_id import index_by_spicedb_object_id

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

-- Org/Space/Project hierarchy — one tier below Workspace
CREATE TABLE IF NOT EXISTS project (
    urn TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    workspace_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS principal_oidc_sub_uidx
    ON principal (oidc_sub) WHERE oidc_sub IS NOT NULL;

-- OIDC authorization state table for PKCE authentication
CREATE TABLE IF NOT EXISTS oidc_pending_state (
    state TEXT PRIMARY KEY,
    verifier TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS saml_seen_assertion (
    assertion_id TEXT PRIMARY KEY,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


# hashlib.scrypt needs an OpenSSL build with scrypt support — absent on
# e.g. macOS's LibreSSL-linked system Python. pbkdf2_hmac has no such gap
# (pure-Python fallback built into hashlib itself) and is still a NIST-
# approved KDF (SP 800-132), so it's the portable stdlib-only choice here.
_PBKDF2_ITERATIONS = 600_000


def hash_client_secret(secret: str) -> str:
    """Self-describing PBKDF2-HMAC-SHA256 hash:
    `pbkdf2$<iterations>$<salt_b64>$<hash_b64>` — stdlib-only (no
    pgcrypto / extra dependency), iteration count travels with the hash
    so it can be raised later without breaking hashes already stored."""
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", secret.encode(), salt, _PBKDF2_ITERATIONS, dklen=32)
    salt_b64 = base64.urlsafe_b64encode(salt).decode()
    hash_b64 = base64.urlsafe_b64encode(derived).decode()
    return f"pbkdf2${_PBKDF2_ITERATIONS}${salt_b64}${hash_b64}"


def _verify_client_secret_hash(candidate: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2":
            return False
        salt = base64.urlsafe_b64decode(salt_b64)
        expected = base64.urlsafe_b64decode(hash_b64)
    except (ValueError, base64.binascii.Error):
        return False
    derived = hashlib.pbkdf2_hmac("sha256", candidate.encode(), salt, int(iterations), dklen=len(expected))
    return secrets.compare_digest(derived, expected)


async def verify_and_migrate_secret(pool: asyncpg.Pool, row: asyncpg.Record, candidate: str) -> bool:
    """Verify `candidate` against a principal row, lazily migrating a
    legacy plaintext `client_secret` to `client_secret_hash` on the
    first successful auth after upgrade — no bulk rewrite at deploy
    time, no forced rotation for dormant principals."""
    stored_hash = row["client_secret_hash"]
    if stored_hash:
        return _verify_client_secret_hash(candidate, stored_hash)
    legacy = row["client_secret"]
    if legacy is None or not secrets.compare_digest(legacy, candidate):
        return False
    await pool.execute(
        "UPDATE principal SET client_secret_hash = $1, client_secret = NULL WHERE urn = $2",
        hash_client_secret(candidate),
        row["urn"],
    )
    return True


def tenant_urn(tenant_id: str) -> str:
    return build_urn(tenant_id, "global", "tenant", tenant_id)


def workspace_urn(tenant_id: str, workspace_id: str) -> str:
    return build_urn(tenant_id, "global", "workspace", workspace_id)


def project_urn(tenant_id: str, workspace_id: str, name: str) -> str:
    return build_urn(tenant_id, workspace_id, "project", name)


VALID_WORKSPACE_RELATIONS = {"viewer", "editor", "admin"}
VALID_PROJECT_RELATIONS = {"viewer", "editor", "admin"}


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(_DDL)


async def ensure_instance_bootstrap(
    pool: asyncpg.Pool,
    authz,
    *,
    tenant_id: str,
    workspace_id: str,
) -> None:
    """Idempotent empty-instance install + orphan recovery — not demo data.

    Always ensures the env bootstrap tenant/workspace rows and the SpiceDB
    `parent_tenant` edge. If the bootstrap workspace has no *usable*
    `workspace.admin` (active principal row matching a SpiceDB admin
    grant), creates/repairs the bootstrap admin principal and grants so
    `/token` and `POST /tenants` are reachable.

    Optional break-glass: `HOLON_BOOTSTRAP_ADMIN_RESET_SECRET=true` plus
    `HOLON_BOOTSTRAP_ADMIN_SECRET` rewrites the bootstrap admin's
    `client_secret` (then unset the reset flag).
    """
    admin_local = (os.environ.get("HOLON_BOOTSTRAP_ADMIN_LOCAL_NAME") or "admin").strip() or "admin"
    admin_urn = build_urn(tenant_id, "global", "user", admin_local)
    t_urn = tenant_urn(tenant_id)
    w_urn = workspace_urn(tenant_id, workspace_id)

    await _ensure_bootstrap_tenant_row(pool, tenant_id)
    await _ensure_bootstrap_workspace_row(pool, tenant_id, workspace_id)

    # Hierarchy edge is cheap and idempotent (OPERATION_TOUCH).
    await authz.write_relationship(
        resource_type="workspace",
        resource_urn=w_urn,
        relation="parent_tenant",
        subject_type="tenant",
        subject_urn=t_urn,
    )

    needs_admin = not await _has_usable_workspace_admin(pool, authz, w_urn)
    if needs_admin:
        secret = _require_bootstrap_admin_secret()
        await _ensure_bootstrap_admin_principal(pool, tenant_id=tenant_id, admin_urn=admin_urn, secret=secret)
        await authz.write_relationship(
            resource_type="tenant", resource_urn=t_urn, relation="member", subject_urn=admin_urn,
        )
        await authz.write_relationship(
            resource_type="workspace", resource_urn=w_urn, relation="admin", subject_urn=admin_urn,
        )
    elif _truthy("HOLON_BOOTSTRAP_ADMIN_RESET_SECRET"):
        secret = _require_bootstrap_admin_secret()
        updated = await pool.execute(
            "UPDATE principal SET client_secret = NULL, client_secret_hash = $1, status = 'active' WHERE urn = $2",
            hash_client_secret(secret),
            admin_urn,
        )
        if updated == "UPDATE 0":
            # Principal missing — fall through to full repair.
            await _ensure_bootstrap_admin_principal(pool, tenant_id=tenant_id, admin_urn=admin_urn, secret=secret)
            await authz.write_relationship(
                resource_type="tenant", resource_urn=t_urn, relation="member", subject_urn=admin_urn,
            )
            await authz.write_relationship(
                resource_type="workspace", resource_urn=w_urn, relation="admin", subject_urn=admin_urn,
            )


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _require_bootstrap_admin_secret() -> str:
    """Unconditional in every environment — no dev-login fallback. A local
    stack bootstraps exactly the same way a production one does: set
    HOLON_BOOTSTRAP_ADMIN_SECRET in .env before first boot."""
    secret = (os.environ.get("HOLON_BOOTSTRAP_ADMIN_SECRET") or "").strip()
    if secret:
        return secret
    raise RuntimeError(
        "empty / unrepaired Identity bootstrap (or HOLON_BOOTSTRAP_ADMIN_RESET_SECRET) "
        "requires HOLON_BOOTSTRAP_ADMIN_SECRET to be set"
    )


async def _ensure_bootstrap_tenant_row(pool: asyncpg.Pool, tenant_id: str) -> None:
    existing = await pool.fetchrow("SELECT tenant_id FROM tenant WHERE tenant_id = $1", tenant_id)
    if existing is not None:
        return
    await pool.execute(
        "INSERT INTO tenant (tenant_id, display_name, status) VALUES ($1, $2, 'active')",
        tenant_id,
        os.environ.get("HOLON_BOOTSTRAP_TENANT_DISPLAY_NAME", tenant_id),
    )


async def _ensure_bootstrap_workspace_row(
    pool: asyncpg.Pool, tenant_id: str, workspace_id: str
) -> None:
    existing = await get_workspace(pool, workspace_id)
    if existing is not None:
        if existing["tenant_id"] != tenant_id:
            raise RuntimeError(
                f"workspace {workspace_id!r} belongs to tenant {existing['tenant_id']!r}, "
                f"expected bootstrap tenant {tenant_id!r}"
            )
        return
    await pool.execute(
        "INSERT INTO workspace (workspace_id, tenant_id, display_name, status) VALUES ($1, $2, $3, 'active')",
        workspace_id,
        tenant_id,
        os.environ.get("HOLON_BOOTSTRAP_WORKSPACE_DISPLAY_NAME", workspace_id),
    )


async def _has_usable_workspace_admin(pool: asyncpg.Pool, authz, workspace_urn_value: str) -> bool:
    """True when at least one SpiceDB workspace.admin maps to an active principal."""
    relationships = await authz.read_relationships(
        resource_type="workspace",
        resource_urn=workspace_urn_value,
        relation="admin",
    )
    if not relationships:
        return False
    rows = await pool.fetch("SELECT urn, status FROM principal")
    by_object_id = index_by_spicedb_object_id(rows)
    for rel in relationships:
        subject = rel.get("subject", {}).get("object", {})
        if subject.get("objectType") != "principal":
            continue
        row = by_object_id.get(subject.get("objectId", ""))
        if row is not None and row["status"] == "active":
            return True
    return False


async def _ensure_bootstrap_admin_principal(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    admin_urn: str,
    secret: str,
) -> None:
    existing = await pool.fetchrow("SELECT urn, status FROM principal WHERE urn = $1", admin_urn)
    if existing is None:
        await pool.execute(
            """
            INSERT INTO principal (urn, type, tenant_id, display_name, on_behalf_of, country, client_secret, client_secret_hash, status)
            VALUES ($1, 'user', $2, $3, NULL, $4, NULL, $5, 'active')
            """,
            admin_urn,
            tenant_id,
            os.environ.get("HOLON_BOOTSTRAP_ADMIN_DISPLAY_NAME", "Instance Admin"),
            os.environ.get("HOLON_BOOTSTRAP_ADMIN_COUNTRY", "FR"),
            hash_client_secret(secret),
        )
        return
    # Re-activate + refresh secret when repairing an orphaned/disabled bootstrap admin.
    await pool.execute(
        "UPDATE principal SET client_secret = NULL, client_secret_hash = $1, status = 'active' WHERE urn = $2",
        hash_client_secret(secret),
        admin_urn,
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
    external_id: Optional[str] = None,
) -> dict:
    type_segment = {"service_account": "service-account", "service-account": "service-account"}.get(type, type)
    urn = build_urn(tenant_id, "global", type_segment, local_name)
    secret = client_secret if client_secret is not None else secrets.token_urlsafe(32)
    db_type = "service_account" if type in ("service_account", "service-account") else type
    await pool.execute(
        """
        INSERT INTO principal (urn, type, tenant_id, display_name, on_behalf_of, country, client_secret, client_secret_hash, status, oidc_sub, external_id)
        VALUES ($1, $2, $3, $4, $5, $6, NULL, $7, 'active', $8, $9)
        """,
        urn,
        db_type,
        tenant_id,
        display_name,
        on_behalf_of,
        country,
        hash_client_secret(secret),
        oidc_sub,
        external_id,
    )
    row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    result = dict(row)
    result["client_secret"] = secret
    return result


async def set_principal_status(pool: asyncpg.Pool, urn: str, status: str) -> Optional[dict]:
    await pool.execute("UPDATE principal SET status = $2 WHERE urn = $1", urn, status)
    row = await pool.fetchrow("SELECT * FROM principal WHERE urn = $1", urn)
    return dict(row) if row else None

