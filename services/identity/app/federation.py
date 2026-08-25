"""Claims mapping shared by OIDC and SAML federated login."""

from __future__ import annotations

import os
from typing import Any


def tenant_from_claims(claims: dict[str, Any], *, default_tenant: str) -> str:
    claim_name = os.environ.get("HOLON_OIDC_TENANT_CLAIM", "tenant_id")
    if claim_name in claims and claims[claim_name]:
        return str(claims[claim_name])
    prefix = os.environ.get("HOLON_OIDC_TENANT_GROUP_PREFIX", "tenant:")
    groups = claims.get("groups") or claims.get("roles") or []
    if isinstance(groups, str):
        groups = [groups]
    for group in groups:
        if isinstance(group, str) and group.startswith(prefix):
            return group[len(prefix) :]
    return default_tenant


def urn_safe_local_name(raw: str, *, fallback: str = "sso-user") -> str:
    """Sanitize IdP-supplied identifier into URN-safe local-name format."""
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in raw)[:64]
    safe = safe.lstrip("-.")
    return safe or fallback


def local_name_from_claims(claims: dict[str, Any]) -> str:
    return urn_safe_local_name(str(claims.get("sub") or "unknown"), fallback="oidc-user")


def display_name_from_claims(claims: dict[str, Any]) -> str:
    return str(claims.get("name") or claims.get("email") or claims.get("preferred_username") or claims.get("sub") or "SSO User")


def groups_from_claims(claims: dict[str, Any]) -> list[str]:
    groups = claims.get("groups") or claims.get("roles") or []
    if isinstance(groups, str):
        return [groups]
    return [str(g) for g in groups]


_ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}


def workspace_roles_from_claims(claims: dict[str, Any]) -> dict[str, str]:
    """Map IdP groups to workspace roles."""
    prefixes = (
        ("admin", os.environ.get("HOLON_OIDC_WORKSPACE_ADMIN_GROUP_PREFIX", "workspace-admin:")),
        ("editor", os.environ.get("HOLON_OIDC_WORKSPACE_EDITOR_GROUP_PREFIX", "workspace-editor:")),
        ("viewer", os.environ.get("HOLON_OIDC_WORKSPACE_GROUP_PREFIX", "workspace:")),
    )
    desired: dict[str, str] = {}
    for group in groups_from_claims(claims):
        for relation, prefix in prefixes:
            if not group.startswith(prefix):
                continue
            workspace_id = group[len(prefix) :]
            if not workspace_id:
                continue
            current = desired.get(workspace_id)
            if current is None or _ROLE_RANK[relation] > _ROLE_RANK[current]:
                desired[workspace_id] = relation
            break
    return desired
