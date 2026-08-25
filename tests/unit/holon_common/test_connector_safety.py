"""Tests for connector SSRF, secret_ref, and Kafka topic guards."""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))

from holon_common.connector_safety import (  # noqa: E402
    ConnectorSafetyError,
    assert_connector_host,
    assert_connector_secret_ref,
    assert_http_url,
    assert_kafka_topic,
    same_origin,
)


def _gai_named(mapping: dict[str, str], *, default: Exception | None = None):
    def fake(host, *a, **k):
        name = (host or "").strip().lower().rstrip(".")
        if name in mapping:
            addr = mapping[name]
            sock_addr = (addr, 0, 0, 0) if ":" in addr else (addr, 0)
            return [(0, 0, 0, "", sock_addr)]
        if default is not None:
            raise default
        raise socket.gaierror("not found")

    return fake


def test_unresolved_host_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai_named({}, default=socket.gaierror("fail")),
    )
    with pytest.raises(ConnectorSafetyError, match="could not be resolved"):
        assert_connector_host("no-such-host.invalid")


def test_loopback_hostname_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai_named({"evil.example": "127.0.0.1"}, default=socket.gaierror("x")),
    )
    with pytest.raises(ConnectorSafetyError, match="blocked address"):
        assert_connector_host("evil.example")


def test_link_local_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai_named({"meta.example": "169.254.169.254"}, default=socket.gaierror("x")),
    )
    with pytest.raises(ConnectorSafetyError, match="blocked address"):
        assert_connector_host("meta.example")


def test_ipv4_mapped_loopback_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai_named({"mapped.example": "::ffff:127.0.0.1"}, default=socket.gaierror("x")),
    )
    with pytest.raises(ConnectorSafetyError, match="blocked address"):
        assert_connector_host("mapped.example")


def test_rfc1918_blocked(monkeypatch) -> None:
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai_named({"internal-app": "10.0.0.5"}, default=socket.gaierror("x")),
    )
    with pytest.raises(ConnectorSafetyError, match="blocked address"):
        assert_connector_host("internal-app")


def test_literal_loopback_ip_blocked() -> None:
    with pytest.raises(ConnectorSafetyError):
        assert_connector_host("127.0.0.1")


def test_allowed_host_may_resolve_private(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_CONNECTOR_ALLOWED_HOSTS", "postgres")
    monkeypatch.setattr(
        "holon_common.connector_safety.socket.getaddrinfo",
        _gai_named({"postgres": "172.18.0.2"}, default=socket.gaierror("x")),
    )
    assert_connector_host("postgres")


def test_blocked_platform_hostname() -> None:
    with pytest.raises(ConnectorSafetyError, match="not allowed"):
        assert_connector_host("identity")


def test_secret_ref_env_platform_prefix() -> None:
    with pytest.raises(ConnectorSafetyError, match="platform secret"):
        assert_connector_secret_ref("HOLON_JWT_SECRET", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="platform secret"):
        assert_connector_secret_ref("env:POSTGRES_PASSWORD", tenant_id="acme")


def test_secret_ref_vault_requires_tenant_prefix() -> None:
    assert_connector_secret_ref("vault:connectors/acme/db#password", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="connectors/"):
        assert_connector_secret_ref("vault:holon/prod/connector-admin#x", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="holon-connector-acme"):
        assert_connector_secret_ref("k8s:acme-platform#PASSWORD", tenant_id="acme")


def test_secret_ref_k8s_tenant_secret_name() -> None:
    assert_connector_secret_ref("k8s:holon-connector-acme#PASSWORD", tenant_id="acme")
    assert_connector_secret_ref("k8s:holon-connector-acme-sql#PASSWORD", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="platform secret"):
        assert_connector_secret_ref("k8s:holon-connector-acme#HOLON_JWT_SECRET", tenant_id="acme")


def test_kafka_holon_topics_reserved() -> None:
    with pytest.raises(ConnectorSafetyError, match="reserved"):
        assert_kafka_topic("holon.events")
    with pytest.raises(ConnectorSafetyError, match="reserved"):
        assert_kafka_topic("holon.identity.out")
    assert_kafka_topic("inventory.updates")


def test_same_origin_normalizes_trailing_dot() -> None:
    assert same_origin("https://api.example.com/v1", "https://api.example.com./v1/page2")


def test_assert_http_url_rejects_non_http() -> None:
    with pytest.raises(ConnectorSafetyError):
        assert_http_url("ftp://files.example.com/x")


def test_unwrap_mapped_ip() -> None:
    # is_loopback on the wrapped form itself is Python-version-dependent
    # (differs between 3.9 and 3.11+) — what actually matters is that
    # assert_connector_host blocks it either way, via _unwrap_ip.
    with pytest.raises(ConnectorSafetyError):
        assert_connector_host("::ffff:127.0.0.1")
