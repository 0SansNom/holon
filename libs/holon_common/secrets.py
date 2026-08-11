"""Pluggable secret resolution (ADR 026 / Phase 3).

We ship adapters that read secrets from the deployer's store — we never
operate Vault/KMS for them.

Reference forms:
  env:NAME                  → os.environ[NAME]
  k8s:namespace/name/key    → Kubernetes Secret data key (in-cluster)
  vault:path#key            → HashiCorp Vault KV v2 secret field
  aws:secret-id|json-key    → AWS Secrets Manager (optional JSON key)

Plain env-var names without a scheme still resolve via EnvSecretProvider
for backward compatibility with HOLON_JWT_SECRET etc.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from typing import Optional, Protocol

logger = logging.getLogger("holon_common.secrets")


class SecretProvider(Protocol):
    def get(self, ref: str) -> str: ...


class EnvSecretProvider:
    """Default / demo provider — reads process environment."""

    def get(self, ref: str) -> str:
        name = ref.removeprefix("env:")
        value = os.environ.get(name)
        if value is None or value == "":
            raise KeyError(f"secret not found in env: {name}")
        return value


class KubernetesSecretProvider:
    """Read a mounted or API-fetched K8s Secret. Prefer mounted files
    at /var/run/secrets/holon/{name}/{key} when present; fall back to
    the Kubernetes API (requires in-cluster config + RBAC).
    """

    def get(self, ref: str) -> str:
        body = ref.removeprefix("k8s:")
        parts = body.split("/")
        if len(parts) != 3:
            raise ValueError(f"k8s secret ref must be k8s:namespace/name/key, got {ref!r}")
        namespace, name, key = parts
        mounted = f"/var/run/secrets/holon/{name}/{key}"
        if os.path.isfile(mounted):
            return open(mounted, encoding="utf-8").read().rstrip("\n")
        # Lazy import — only needed when actually resolving k8s refs.
        try:
            from kubernetes import client, config
        except ImportError as exc:
            raise RuntimeError("kubernetes package required for k8s: secret refs") from exc
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        secret = client.CoreV1Api().read_namespaced_secret(name, namespace)
        raw = (secret.data or {}).get(key)
        if raw is None:
            raise KeyError(f"key {key!r} missing in secret {namespace}/{name}")
        return base64.b64decode(raw).decode("utf-8")


class VaultSecretProvider:
    """HashiCorp Vault KV v2. Uses VAULT_ADDR + VAULT_TOKEN (or
    VAULT_ROLE_ID/VAULT_SECRET_ID for AppRole) from the environment —
    the deployer injects those; we do not run Vault.
    """

    def get(self, ref: str) -> str:
        body = ref.removeprefix("vault:")
        if "#" not in body:
            raise ValueError(f"vault secret ref must be vault:path#key, got {ref!r}")
        path, key = body.split("#", 1)
        addr = os.environ.get("VAULT_ADDR")
        if not addr:
            raise RuntimeError("VAULT_ADDR required for vault: secret refs")
        import httpx

        token = os.environ.get("VAULT_TOKEN")
        if not token:
            raise RuntimeError("VAULT_TOKEN required for vault: secret refs")
        # KV v2: secret/data/<path>
        url = f"{addr.rstrip('/')}/v1/{path}"
        if "/data/" not in path:
            # allow vault:secret/data/holon#jwt or vault:secret/holon#jwt
            mount, _, rest = path.partition("/")
            url = f"{addr.rstrip('/')}/v1/{mount}/data/{rest}"
        response = httpx.get(url, headers={"X-Vault-Token": token}, timeout=10.0)
        response.raise_for_status()
        data = response.json().get("data", {}).get("data") or response.json().get("data") or {}
        if key not in data:
            raise KeyError(f"key {key!r} missing in vault path {path}")
        return str(data[key])


class AwsSecretsManagerProvider:
    """AWS Secrets Manager. Optional `|json-key` when the secret string is JSON."""

    def get(self, ref: str) -> str:
        body = ref.removeprefix("aws:")
        secret_id, _, json_key = body.partition("|")
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 required for aws: secret refs") from exc
        client = boto3.client("secretsmanager")
        raw = client.get_secret_value(SecretId=secret_id)["SecretString"]
        if not json_key:
            return raw
        parsed = json.loads(raw)
        if json_key not in parsed:
            raise KeyError(f"json key {json_key!r} missing in AWS secret {secret_id}")
        return str(parsed[json_key])


def build_secret_provider(backend: Optional[str] = None) -> SecretProvider:
    """HOLON_SECRET_BACKEND = env | kubernetes | vault | aws (default env)."""
    name = (backend or os.environ.get("HOLON_SECRET_BACKEND") or "env").lower()
    if name == "env":
        return EnvSecretProvider()
    if name == "kubernetes":
        return KubernetesSecretProvider()
    if name == "vault":
        return VaultSecretProvider()
    if name == "aws":
        return AwsSecretsManagerProvider()
    raise ValueError(f"unknown HOLON_SECRET_BACKEND: {name!r}")


_PROVIDER: Optional[SecretProvider] = None


def get_secret(ref: str) -> str:
    """Resolve a secret reference. Scheme-less refs are treated as env:NAME."""
    global _PROVIDER
    if _PROVIDER is None:
        _PROVIDER = build_secret_provider()
    if ":" not in ref:
        ref = f"env:{ref}"
    scheme = ref.split(":", 1)[0]
    # Route by scheme so a single provider process can still resolve env:
    # refs for bootstrap while HOLON_SECRET_BACKEND=vault for others.
    if scheme == "env":
        return EnvSecretProvider().get(ref)
    if scheme == "k8s":
        return KubernetesSecretProvider().get(ref)
    if scheme == "vault":
        return VaultSecretProvider().get(ref)
    if scheme == "aws":
        return AwsSecretsManagerProvider().get(ref)
    return _PROVIDER.get(ref)


def resolve_optional(ref: Optional[str]) -> Optional[str]:
    if ref is None or ref == "":
        return None
    return get_secret(ref)
