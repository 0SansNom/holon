"""Guards for no-code connector configuration (SSRF, secret exfil, bus ACL)."""

from __future__ import annotations

import ipaddress
import os
import re
import socket
from typing import Optional
from urllib.parse import urlsplit

# Platform service DNS names on the compose/cluster network. Connecting a
# tenant connector here is SSRF into Holon itself. `postgres` is NOT in
# this set: demo/source DBs share that hostname (`source_erp`).
_BLOCKED_HOSTS = frozenset(
    {
        "spicedb",
        "opa",
        "minio",
        "opensearch",
        "qdrant",
        "redpanda",
        "iceberg-rest",
        "identity",
        "connectivity",
        "knowledge",
        "experience",
        "automation",
        "intelligence",
        "metadata.google.internal",
        "metadata.google.com",
        "instance-data",
    }
)

_BLOCKED_ENV_PREFIXES = (
    "HOLON_JWT",
    "HOLON_SPICEDB",
    "HOLON_BOOTSTRAP",
    "HOLON_SCIM",
    "HOLON_OIDC",
    "HOLON_SAML",
    "HOLON_METRICS",
    "HOLON_DB",
    "HOLON_SOURCE",
    "HOLON_MONGO",
    "HOLON_S3",
    "HOLON_ICEBERG",
    "HOLON_KAFKA",
    "HOLON_OPENSEARCH",
    "HOLON_QDRANT",
    "HOLON_IDENTITY",
    "HOLON_CONNECTIVITY",
    "HOLON_KNOWLEDGE",
    "HOLON_EXPERIENCE",
    "HOLON_AUTOMATION",
    "HOLON_INTELLIGENCE",
    "POSTGRES",
    "DATABASE",
    "MONGO",
    "MYSQL",
    "REDIS",
    "AWS_SECRET",
    "AWS_ACCESS",
    "MINIO",
    "OPENSEARCH",
    "ANTHROPIC",
    "VOYAGE",
    "VAULT_",
    "K8S_",
)

_PLATFORM_KAFKA_TOPICS = frozenset({"holon.events", "__consumer_offsets"})


class ConnectorSafetyError(ValueError):
    pass


def _hostname(host: str) -> str:
    return (host or "").strip().lower().rstrip(".")


def _allowed_hosts() -> set[str]:
    raw = os.environ.get("HOLON_CONNECTOR_ALLOWED_HOSTS") or ""
    return {_hostname(part) for part in raw.split(",") if part.strip()}


def _blocked_hosts() -> set[str]:
    hosts = set(_BLOCKED_HOSTS)
    for var in (
        "HOLON_SPICEDB_URL",
        "HOLON_OPA_URL",
        "HOLON_OPENSEARCH_URL",
        "HOLON_QDRANT_URL",
        "HOLON_KAFKA_BOOTSTRAP",
        "HOLON_S3_ENDPOINT",
        "HOLON_ICEBERG_CATALOG_URI",
        "HOLON_IDENTITY_URL",
        "HOLON_CONNECTIVITY_URL",
        "HOLON_KNOWLEDGE_URL",
        "HOLON_EXPERIENCE_URL",
        "HOLON_AUTOMATION_URL",
        "HOLON_INTELLIGENCE_URL",
    ):
        raw = (os.environ.get(var) or "").strip()
        if not raw:
            continue
        parsed = urlsplit(raw if "://" in raw else f"//{raw}", allow_fragments=False)
        if parsed.hostname:
            hosts.add(_hostname(parsed.hostname))
    return hosts


def _unwrap_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool) -> bool:
    ip = _unwrap_ip(ip)
    if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return True
    if not allow_private and (ip.is_private or ip.is_reserved):
        return True
    return False


def _resolve_ips(host: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    name = _hostname(host)
    try:
        infos = socket.getaddrinfo(name, None)
    except socket.gaierror as exc:
        raise ConnectorSafetyError(f"host {host!r} could not be resolved") from exc
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for info in infos:
        addr = info[4][0]
        try:
            ips.append(_unwrap_ip(ipaddress.ip_address(addr)))
        except ValueError:
            continue
    if not ips:
        raise ConnectorSafetyError(f"host {host!r} could not be resolved")
    return ips


def _platform_blocked_ips() -> set[str]:
    blocked: set[str] = set()
    for host in _blocked_hosts():
        try:
            for ip in _resolve_ips(host):
                blocked.add(str(ip))
        except ConnectorSafetyError:
            continue
    return blocked


def assert_connector_host(host: str, *, resolve: bool = True) -> None:
    """Reject platform / private / loopback targets for tenant connectors.

    When ``resolve`` is False (config registration), only the hostname
    blocklist and IP literals are checked — DNS failures are deferred to
    fetch so admins can save a source before the remote host is up.
    """
    name = _hostname(host)
    if not name:
        raise ConnectorSafetyError("host is required")
    if name in _blocked_hosts() or name.endswith(".internal"):
        raise ConnectorSafetyError(f"host {host!r} is not allowed for connectors")

    allow_private = name in _allowed_hosts()
    platform_ips = _platform_blocked_ips() if resolve else set()

    try:
        literal = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        literal = None

    if literal is not None:
        ip = _unwrap_ip(literal)
        if _is_blocked_ip(ip, allow_private=allow_private) or str(ip) in platform_ips:
            raise ConnectorSafetyError(f"host {host!r} resolves to a blocked address")
        return

    if not resolve:
        return

    for ip in _resolve_ips(name):
        if _is_blocked_ip(ip, allow_private=allow_private) or str(ip) in platform_ips:
            raise ConnectorSafetyError(f"host {host!r} resolves to a blocked address")


def assert_http_url(url: str, *, resolve: bool = True) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ConnectorSafetyError("URL must be http or https")
    if not parsed.hostname:
        raise ConnectorSafetyError("URL missing host")
    assert_connector_host(parsed.hostname, resolve=resolve)


def same_origin(left: str, right: str) -> bool:
    a, b = urlsplit(left), urlsplit(right)
    return (a.scheme, _hostname(a.hostname or ""), a.port) == (b.scheme, _hostname(b.hostname or ""), b.port)


# HOLON_CONN_<TENANT>__<KEY>: the tenant segment never contains "__", so the
# first "__" is an unambiguous delimiter (no tenant can claim a peer's names).
_TENANT_ENV_NAME_RE = re.compile(r"^HOLON_CONN_([A-Z0-9]+(?:_[A-Z0-9]+)*)__([A-Z0-9][A-Z0-9_]*)$")
_TENANT_ENV_SLUG_RE = re.compile(r"^[A-Z0-9]+(?:_[A-Z0-9]+)*$")


def _tenant_env_slug(tenant_id: str) -> str:
    """Map a tenant id to its HOLON_CONN_<TENANT>__* segment ('-' → '_').

    Injective only when the result has no '__' and no edge '_' (tenant ids
    with '--' or a trailing '-'); those tenants must use vault:/k8s:/aws:.
    """
    slug = (tenant_id or "").strip().upper().replace("-", "_")
    if not _TENANT_ENV_SLUG_RE.match(slug):
        raise ConnectorSafetyError(
            f"tenant {tenant_id!r} cannot use env: secret_refs — use vault:/k8s:/aws: instead"
        )
    return slug


def _assert_not_platform_secret_name(name: str) -> None:
    upper = name.upper()
    for prefix in _BLOCKED_ENV_PREFIXES:
        if upper == prefix.rstrip("_") or upper.startswith(prefix):
            raise ConnectorSafetyError("secret_ref must not resolve a platform secret")


def _assert_env_secret_name(name: str, *, tenant_id: str) -> None:
    """Restrict env: refs so tenants cannot read platform or peer secrets.

    Production: only ``HOLON_CONN_<TENANT>__*`` (tenant-scoped allowlist).
    Non-production: same allowlist *or* a name that is not a blocked platform
    prefix (keeps local ``env:ERP_PASSWORD`` demos working).
    """
    from .security_posture import is_production

    if not tenant_id or not str(tenant_id).strip():
        raise ConnectorSafetyError("secret_ref requires a tenant_id")
    upper = (name or "").strip().upper()
    if not upper:
        raise ConnectorSafetyError("env secret_ref name is required")
    slug = _tenant_env_slug(tenant_id)
    expected = f"HOLON_CONN_{slug}__"
    match = _TENANT_ENV_NAME_RE.match(upper)
    if match and match.group(1) == slug:
        return
    if is_production():
        raise ConnectorSafetyError(
            f"env secret_ref in production must start with {expected!r} "
            "(or use vault:/k8s:/aws: with a tenant-scoped path)"
        )
    _assert_not_platform_secret_name(name)
    # Non-prod still forbids any other HOLON_* (platform process env).
    if upper.startswith("HOLON_"):
        raise ConnectorSafetyError(
            f"env secret_ref must start with {expected!r} — other HOLON_* vars are platform secrets"
        )


def assert_no_inline_connector_secret(value: Optional[str], *, field: str) -> None:
    """Refuse plaintext connector secrets in the request body when production.

    Existing stored values may be kept on edit (caller omits the field).
    Rotate them via secret_ref.
    """
    if value is None or not str(value).strip():
        return
    from .security_posture import is_production

    if is_production():
        raise ConnectorSafetyError(
            f"{field} cannot be sent in the request body in production — use secret_ref"
        )


def assert_production_requires_secret_ref(ref: Optional[str], *, is_update: bool) -> None:
    """Brand-new connector credentials in production must be a secret_ref.

    Edits may omit the field to keep a previously stored value (plaintext
    included) until the operator rotates onto secret_ref.
    """
    if is_update:
        return
    if ref is not None and str(ref).strip():
        return
    from .security_posture import is_production

    if is_production():
        raise ConnectorSafetyError("secret_ref is required in production")


# Parsers below split refs exactly like holon_common.secrets' providers do,
# so a ref that passes the guard is one the provider will actually resolve.
_SECRET_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# RFC 1123 label (namespace) / subdomain (Secret name).
_K8S_NAMESPACE_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _assert_secret_key(key: str, *, what: str) -> None:
    if not key or not _SECRET_KEY_RE.match(key) or key in {".", ".."}:
        raise ConnectorSafetyError(f"{what} must be a plain key name, got {key!r}")
    _assert_not_platform_secret_name(key)


def _assert_vault_ref(body: str, *, tenant_id: str) -> None:
    """vault:connectors/<tenant>/<path>#key (VaultSecretProvider form)."""
    path, sep, key = body.partition("#")
    if not sep:
        raise ConnectorSafetyError("vault secret_ref must be vault:connectors/<tenant>/<path>#key")
    prefix = f"connectors/{tenant_id}/"
    segments = path.split("/")
    if not path.startswith(prefix) or any(seg in {"", ".", ".."} for seg in segments):
        raise ConnectorSafetyError(f"vault secret_ref must start with {prefix!r} (no empty or '..' segments)")
    _assert_secret_key(key, what="vault secret_ref key")


def _assert_k8s_ref(body: str, *, tenant_id: str) -> None:
    """k8s:<namespace>/holon-connector-<tenant>[.<suffix>]/<key> (KubernetesSecretProvider form).

    '.' delimits the suffix: tenant ids are [a-z0-9-], so a '-' suffix would
    let tenant "acme" claim "holon-connector-acme-corp".
    """
    parts = body.split("/")
    expected = f"holon-connector-{tenant_id}"
    if len(parts) != 3:
        raise ConnectorSafetyError(f"k8s secret_ref must be k8s:<namespace>/{expected}[.<suffix>]/<key>")
    namespace, name, key = parts
    if not _K8S_NAMESPACE_RE.match(namespace):
        raise ConnectorSafetyError(f"invalid k8s namespace {namespace!r} in secret_ref")
    if name != expected and not (name.startswith(f"{expected}.") and _SECRET_KEY_RE.match(name)):
        raise ConnectorSafetyError(f"k8s secret_ref name must be {expected} or {expected}.<suffix>")
    _assert_secret_key(key, what="k8s secret_ref key")


def _assert_aws_ref(body: str, *, tenant_id: str) -> None:
    """aws:<secret-id>[|json-key] (AwsSecretsManagerProvider form).

    secret-id must be connectors/<tenant>/... or holon-connector-<tenant>;
    ARNs are refused since they can name a secret in another account.
    """
    secret_id, sep, json_key = body.partition("|")
    expected = f"holon-connector-{tenant_id}"
    prefix = f"connectors/{tenant_id}/"
    if secret_id.startswith("arn:"):
        raise ConnectorSafetyError(f"aws secret_ref must use a secret name ({prefix}... or {expected}), not an ARN")
    segments = secret_id.split("/")
    if not (secret_id == expected or secret_id.startswith(prefix)) or any(
        seg in {"", ".", ".."} for seg in segments
    ):
        raise ConnectorSafetyError(f"aws secret_ref must start with {prefix!r} or be {expected}")
    if sep:
        _assert_secret_key(json_key, what="aws secret_ref json key")


def assert_connector_secret_ref(ref: Optional[str], *, tenant_id: str) -> None:
    """Tenant-supplied secret_ref must not resolve platform credentials."""
    if ref is None or ref == "":
        return
    if ":" not in ref:
        ref = f"env:{ref}"
    scheme, rest = ref.split(":", 1)
    if scheme == "env":
        name = rest.removeprefix("env:") if rest.startswith("env:") else rest
        _assert_env_secret_name(name, tenant_id=tenant_id)
        return
    if scheme in {"vault", "k8s", "aws"}:
        if not tenant_id:
            raise ConnectorSafetyError("secret_ref requires a tenant_id")
        if scheme == "vault":
            _assert_vault_ref(rest, tenant_id=tenant_id)
        elif scheme == "k8s":
            _assert_k8s_ref(rest, tenant_id=tenant_id)
        else:
            _assert_aws_ref(rest, tenant_id=tenant_id)
        return
    raise ConnectorSafetyError(f"unsupported secret_ref scheme: {scheme!r}")


def resolve_connector_secret(ref: Optional[str], *, tenant_id: str) -> Optional[str]:
    """Re-check a stored secret_ref at use time, then resolve it.

    Refs saved before a guard tightened must not keep resolving platform or
    peer-tenant secrets, so validation runs on every fetch, not only on save.
    """
    if ref is None or ref == "":
        return None
    assert_connector_secret_ref(ref, tenant_id=tenant_id)
    from .secrets import get_secret

    return get_secret(ref)


def assert_kafka_topic(topic: str) -> None:
    if not topic or not topic.strip():
        raise ConnectorSafetyError("topic is required")
    name = topic.strip()
    if name in _PLATFORM_KAFKA_TOPICS or name.startswith("holon."):
        raise ConnectorSafetyError(f"topic {topic!r} is reserved for the platform event bus")
    configured = (os.environ.get("HOLON_KAFKA_TOPIC") or "").strip()
    if configured and name == configured:
        raise ConnectorSafetyError(f"topic {topic!r} is reserved for the platform event bus")
