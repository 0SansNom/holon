"""Unit tests for Identity OIDC group→role mapping and JWT secret refs."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "identity"))

from app import oidc  # noqa: E402
from holon_common.auth import load_jwt_secrets  # noqa: E402


def test_workspace_roles_highest_privilege_wins(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_OIDC_WORKSPACE_ADMIN_GROUP_PREFIX", raising=False)
    monkeypatch.delenv("HOLON_OIDC_WORKSPACE_EDITOR_GROUP_PREFIX", raising=False)
    monkeypatch.delenv("HOLON_OIDC_WORKSPACE_GROUP_PREFIX", raising=False)
    roles = oidc.workspace_roles_from_claims(
        {
            "groups": [
                "workspace:main",
                "workspace-editor:main",
                "workspace-admin:filiale",
                "workspace:other",
            ]
        }
    )
    assert roles["main"] == "editor"
    assert roles["filiale"] == "admin"
    assert roles["other"] == "viewer"


def test_jwt_secret_resolves_env_ref(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    monkeypatch.delenv("HOLON_JWT_ALG", raising=False)
    monkeypatch.setenv("HOLON_JWT_SECRET", "env:HOLON_JWT_SECRET_VALUE")
    monkeypatch.setenv("HOLON_JWT_SECRET_VALUE", "resolved-secret-value")
    secrets, active = load_jwt_secrets()
    assert active == "default"
    assert secrets["default"] == "resolved-secret-value"


def test_jwt_key_map_value_resolves_env_ref(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    monkeypatch.delenv("HOLON_JWT_ALG", raising=False)
    monkeypatch.setenv("HOLON_JWT_SECRETS", json.dumps({"k1": "env:MY_HMAC"}))
    monkeypatch.setenv("HOLON_JWT_ACTIVE_KID", "k1")
    monkeypatch.setenv("MY_HMAC", "map-secret")
    secrets, active = load_jwt_secrets()
    assert active == "k1"
    assert secrets["k1"] == "map-secret"


def test_vault_approle_login(monkeypatch) -> None:
    import types

    from holon_common.secrets import VaultSecretProvider

    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    monkeypatch.setenv("VAULT_ADDR", "http://vault.test")
    monkeypatch.setenv("VAULT_ROLE_ID", "role")
    monkeypatch.setenv("VAULT_SECRET_ID", "secret")

    login = MagicMock()
    login.raise_for_status = MagicMock()
    login.json.return_value = {"auth": {"client_token": "approle-token"}}

    read = MagicMock()
    read.raise_for_status = MagicMock()
    read.json.return_value = {"data": {"data": {"jwt": "from-vault"}}}

    fake_httpx = types.ModuleType("httpx")
    fake_httpx.post = MagicMock(return_value=login)
    fake_httpx.get = MagicMock(return_value=read)
    monkeypatch.setitem(sys.modules, "httpx", fake_httpx)

    value = VaultSecretProvider().get("vault:secret/holon#jwt")
    assert value == "from-vault"
    fake_httpx.post.assert_called_once()
    assert fake_httpx.get.call_args.kwargs["headers"]["X-Vault-Token"] == "approle-token"
