"""Multi-org (filiale) isolation — ADR 026.

Bootstrap admin creates tenant + filiale principal, then a workspace with
`initial_admin_urn` (never grants ReBAC to the bootstrap principal across
tenants). Cross-service checks follow.
"""

from __future__ import annotations

from conftest import CONNECTIVITY, IDENTITY, KNOWLEDGE, _request, _unique_name


def _provision_filiale(msmith_token: str) -> tuple[str, str]:
    suffix = _unique_name("fil").replace("_", "")[-10:].lower()
    tenant_id = f"f{suffix}"
    workspace_id = f"w{suffix}"
    local_name = f"u{suffix}"

    status, body = _request(
        "POST",
        f"{IDENTITY}/tenants",
        token=msmith_token,
        body={"tenant_id": tenant_id, "display_name": f"Filiale {tenant_id}"},
    )
    assert status == 201, body

    # Principal before workspace — same-tenant admin for the new workspace.
    status, body = _request(
        "POST",
        f"{IDENTITY}/principals",
        token=msmith_token,
        body={
            "tenant_id": tenant_id,
            "type": "user",
            "local_name": local_name,
            "display_name": "Filiale User",
            "country": "FR",
        },
    )
    assert status == 201, body
    filiale_urn = body["urn"]
    filiale_secret = body["client_secret"]

    status, body = _request(
        "POST",
        f"{IDENTITY}/workspaces",
        token=msmith_token,
        body={
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "display_name": f"WS {workspace_id}",
            "initial_admin_urn": filiale_urn,
        },
    )
    assert status == 201, body

    status, tok = _request(
        "POST",
        f"{IDENTITY}/token",
        body={"principal_urn": filiale_urn, "client_secret": filiale_secret},
    )
    assert status == 200, tok
    return tenant_id, tok["access_token"]


def test_filiale_isolation_across_services(msmith_token: str) -> None:
    tenant_id, filiale_token = _provision_filiale(msmith_token)

    status, acme_list = _request("GET", f"{IDENTITY}/principals", token=msmith_token)
    assert status == 200, acme_list
    assert all(p["tenant_id"] == "acme" for p in acme_list), acme_list

    status, filiale_list = _request("GET", f"{IDENTITY}/principals", token=filiale_token)
    assert status == 200, filiale_list
    assert all(p["tenant_id"] == tenant_id for p in filiale_list), filiale_list
    assert not any(p["urn"].endswith(":user:jdoe") for p in filiale_list), filiale_list

    status, objects = _request("GET", f"{KNOWLEDGE}/objects/Customer", token=filiale_token)
    assert status == 403, objects
    assert "tenant mismatch" in str(objects.get("detail", objects)).lower() or "denied" in str(
        objects.get("detail", objects)
    ).lower(), objects

    status, sync_body = _request(
        "POST",
        f"{CONNECTIVITY}/sync",
        token=filiale_token,
        body={"dataset": "customers"},
        timeout=60,
    )
    # Filiale has no ReBAC grant on Connectivity's bootstrap workspace —
    # workspace authorize fails closed before dataset lookup.
    assert status == 403, sync_body

    status, sources = _request("GET", f"{CONNECTIVITY}/sources", token=filiale_token)
    assert status == 403, sources


def test_workspace_rejects_cross_tenant_admin_grant(msmith_token: str) -> None:
    """Instance admin must not become workspace admin on a filiale."""
    suffix = _unique_name("x").replace("_", "")[-10:].lower()
    tenant_id = f"t{suffix}"
    status, body = _request(
        "POST",
        f"{IDENTITY}/tenants",
        token=msmith_token,
        body={"tenant_id": tenant_id, "display_name": tenant_id},
    )
    assert status == 201, body

    status, body = _request(
        "POST",
        f"{IDENTITY}/workspaces",
        token=msmith_token,
        body={
            "tenant_id": tenant_id,
            "workspace_id": f"w{suffix}",
            "display_name": "orphan",
            # missing initial_admin_urn — and msmith is acme, not this tenant
        },
    )
    assert status == 400, body
    assert "initial_admin_urn" in str(body["detail"]), body
