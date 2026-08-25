"""Guards for no-code connector configuration (SSRF, secret exfil, bus ACL)."""

from __future__ import annotations

import ipaddress
import os
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
    "POSTGRES",
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


def assert_connector_host(host: str) -> None:
    name = _hostname(host)
    if not name:
        raise ConnectorSafetyError("host is required")
    if name in _blocked_hosts() or name.endswith(".internal"):
        raise ConnectorSafetyError(f"host {host!r} is not allowed for connectors")

    allow_private = name in _allowed_hosts()
    platform_ips = _platform_blocked_ips()

    try:
        literal = ipaddress.ip_address(name.strip("[]"))
    except ValueError:
        literal = None

    if literal is not None:
        ip = _unwrap_ip(literal)
        if _is_blocked_ip(ip, allow_private=allow_private) or str(ip) in platform_ips:
            raise ConnectorSafetyError(f"host {host!r} resolves to a blocked address")
        return

    for ip in _resolve_ips(name):
        if _is_blocked_ip(ip, allow_private=allow_private) or str(ip) in platform_ips:
            raise ConnectorSafetyError(f"host {host!r} resolves to a blocked address")


def assert_http_url(url: str) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise ConnectorSafetyError("URL must be http or https")
    if not parsed.hostname:
        raise ConnectorSafetyError("URL missing host")
    assert_connector_host(parsed.hostname)


def same_origin(left: str, right: str) -> bool:
    a, b = urlsplit(left), urlsplit(right)
    return (a.scheme, _hostname(a.hostname or ""), a.port) == (b.scheme, _hostname(b.hostname or ""), b.port)


def _assert_not_platform_secret_name(name: str) -> None:
    upper = name.upper()
    for prefix in _BLOCKED_ENV_PREFIXES:
        if upper == prefix.rstrip("_") or upper.startswith(prefix):
            raise ConnectorSafetyError("secret_ref must not resolve a platform secret")


def assert_connector_secret_ref(ref: Optional[str], *, tenant_id: str) -> None:
    """Tenant-supplied secret_ref must not resolve platform credentials."""
    if ref is None or ref == "":
        return
    if ":" not in ref:
        ref = f"env:{ref}"
    scheme, rest = ref.split(":", 1)
    if scheme == "env":
        name = rest.removeprefix("env:") if rest.startswith("env:") else rest
        _assert_not_platform_secret_name(name)
        return
    if scheme in {"vault", "k8s", "aws"}:
        if not tenant_id:
            raise ConnectorSafetyError("secret_ref requires a tenant_id")
        path, _, key = rest.partition("#")
        path = path.strip()
        key = key.strip()
        if scheme == "vault":
            prefix = f"connectors/{tenant_id}/"
            if not path.startswith(prefix):
                raise ConnectorSafetyError(f"vault secret_ref must start with {prefix!r}")
        elif scheme == "k8s":
            expected = f"holon-connector-{tenant_id}"
            if path != expected and not path.startswith(f"{expected}-"):
                raise ConnectorSafetyError(
                    f"k8s secret_ref must be {expected} or {expected}-<suffix>"
                )
        else:
            expected = f"holon-connector-{tenant_id}"
            prefix = f"connectors/{tenant_id}/"
            if not (path.startswith(prefix) or path == expected or path.startswith(f"{expected}-")):
                raise ConnectorSafetyError(
                    f"aws secret_ref must start with {prefix!r} or {expected}"
                )
        if key:
            _assert_not_platform_secret_name(key)
        return
    raise ConnectorSafetyError(f"unsupported secret_ref scheme: {scheme!r}")


def assert_kafka_topic(topic: str) -> None:
    if not topic or not topic.strip():
        raise ConnectorSafetyError("topic is required")
    name = topic.strip()
    if name in _PLATFORM_KAFKA_TOPICS or name.startswith("holon."):
        raise ConnectorSafetyError(f"topic {topic!r} is reserved for the platform event bus")
    configured = (os.environ.get("HOLON_KAFKA_TOPIC") or "").strip()
    if configured and name == configured:
        raise ConnectorSafetyError(f"topic {topic!r} is reserved for the platform event bus")
