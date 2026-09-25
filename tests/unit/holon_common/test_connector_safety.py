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
    assert_destination_change_requires_secret,
    assert_http_url,
    assert_kafka_topic,
    assert_no_inline_connector_secret,
    assert_production_requires_secret_ref,
    resolve_connector_secret,
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
    with pytest.raises(ConnectorSafetyError, match="platform secret|HOLON_CONN_ACME__"):
        assert_connector_secret_ref("env:HOLON_SOURCE_DB_URL", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="platform secret|HOLON_CONN_ACME__"):
        assert_connector_secret_ref("env:HOLON_MONGO_URL", tenant_id="acme")


def test_secret_ref_env_tenant_allowlist(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    assert_connector_secret_ref("env:HOLON_CONN_ACME__ERP_PASSWORD", tenant_id="acme")
    assert_connector_secret_ref("env:ERP_PASSWORD", tenant_id="acme")  # non-prod demo names OK
    monkeypatch.setenv("HOLON_ENV", "production")
    assert_connector_secret_ref("env:HOLON_CONN_ACME__ERP_PASSWORD", tenant_id="acme")
    assert_connector_secret_ref("env:HOLON_CONN_ACME_CORP__ERP", tenant_id="acme-corp")
    with pytest.raises(ConnectorSafetyError, match="HOLON_CONN_ACME__"):
        assert_connector_secret_ref("env:ERP_PASSWORD", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="HOLON_CONN_ACME__"):
        assert_connector_secret_ref("env:HOLON_CONN_OTHER__ERP", tenant_id="acme")


@pytest.mark.parametrize(
    "name",
    [
        "HOLON_CONN_ACME_CORP__ERP_PASSWORD",  # acme-corp's secret
        "HOLON_CONN_ACME___ERP_PASSWORD",  # would be tenant "acme-"
        "HOLON_CONN_ACME_ERP_PASSWORD",  # old single-underscore shape
    ],
)
def test_secret_ref_env_tenant_prefix_cannot_claim_peer(monkeypatch, name) -> None:
    monkeypatch.setenv("HOLON_ENV", "production")
    with pytest.raises(ConnectorSafetyError, match="HOLON_CONN_ACME__"):
        assert_connector_secret_ref(f"env:{name}", tenant_id="acme")


def test_secret_ref_env_ambiguous_tenant_ids_must_use_vault(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_ENV", "production")
    for tenant in ("a--b", "acme-"):
        with pytest.raises(ConnectorSafetyError, match="vault"):
            assert_connector_secret_ref("env:HOLON_CONN_A__B__X", tenant_id=tenant)


def test_resolve_connector_secret_rechecks_stored_ref(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_SOURCE_DB_URL", "postgresql://holon:pw@postgres/source_erp")
    monkeypatch.setenv("HOLON_CONN_ACME__ERP_PASSWORD", "s3cret")
    with pytest.raises(ConnectorSafetyError):
        resolve_connector_secret("env:HOLON_SOURCE_DB_URL", tenant_id="acme")
    assert resolve_connector_secret("env:HOLON_CONN_ACME__ERP_PASSWORD", tenant_id="acme") == "s3cret"
    assert resolve_connector_secret(None, tenant_id="acme") is None


def test_secret_ref_vault_requires_tenant_prefix() -> None:
    assert_connector_secret_ref("vault:connectors/acme/db#password", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="connectors/"):
        assert_connector_secret_ref("vault:holon/prod/connector-admin#x", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="connectors/"):
        assert_connector_secret_ref("vault:connectors/acme/../other/db#password", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="vault:connectors"):
        assert_connector_secret_ref("vault:connectors/acme/db", tenant_id="acme")  # no #key


def test_secret_ref_k8s_uses_provider_namespace_name_key_form() -> None:
    assert_connector_secret_ref("k8s:holon/holon-connector-acme/PASSWORD", tenant_id="acme")
    assert_connector_secret_ref("k8s:holon/holon-connector-acme.sql/password", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="holon-connector-acme"):
        assert_connector_secret_ref("k8s:holon/holon-connector-acme-corp/PASSWORD", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="holon-connector-acme"):
        assert_connector_secret_ref("k8s:holon/acme-platform/PASSWORD", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="platform secret"):
        assert_connector_secret_ref("k8s:holon/holon-connector-acme/HOLON_JWT_SECRET", tenant_id="acme")
    # The old guard-only shape never resolved (provider needs namespace/name/key).
    with pytest.raises(ConnectorSafetyError, match="k8s:<namespace>"):
        assert_connector_secret_ref("k8s:holon-connector-acme#PASSWORD", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="namespace"):
        assert_connector_secret_ref("k8s:Bad_NS/holon-connector-acme/PASSWORD", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="plain key"):
        assert_connector_secret_ref("k8s:holon/holon-connector-acme/..", tenant_id="acme")


def test_secret_ref_aws_uses_provider_pipe_json_key_form() -> None:
    assert_connector_secret_ref("aws:connectors/acme/db", tenant_id="acme")
    assert_connector_secret_ref("aws:connectors/acme/db|password", tenant_id="acme")
    assert_connector_secret_ref("aws:holon-connector-acme|password", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="holon-connector-acme"):
        assert_connector_secret_ref("aws:holon-connector-acme-corp|password", tenant_id="acme")
    with pytest.raises(ConnectorSafetyError, match="ARN"):
        assert_connector_secret_ref(
            "aws:arn:aws:secretsmanager:eu-west-1:111122223333:secret:connectors/acme/db", tenant_id="acme"
        )
    with pytest.raises(ConnectorSafetyError, match="platform secret"):
        assert_connector_secret_ref("aws:connectors/acme/db|HOLON_JWT_SECRET", tenant_id="acme")
    # The old guard-only '#' form: the provider splits on '|', so this whole
    # string would be the secret id — not one this tenant owns.
    with pytest.raises(ConnectorSafetyError, match="connectors/acme/"):
        assert_connector_secret_ref("aws:holon-connector-acme#PASSWORD", tenant_id="acme")


def test_guard_and_provider_parse_refs_the_same_way(monkeypatch) -> None:
    """A ref the guard accepts must reach the provider with the same parts."""
    from holon_common import secrets

    seen: dict[str, tuple] = {}

    class FakeK8s:
        def get(self, ref: str) -> str:
            namespace, name, key = ref.removeprefix("k8s:").split("/")
            seen["k8s"] = (namespace, name, key)
            return "v"

    class FakeAws:
        def get(self, ref: str) -> str:
            secret_id, _, json_key = ref.removeprefix("aws:").partition("|")
            seen["aws"] = (secret_id, json_key)
            return "v"

    monkeypatch.setattr(secrets, "KubernetesSecretProvider", FakeK8s)
    monkeypatch.setattr(secrets, "AwsSecretsManagerProvider", FakeAws)
    assert resolve_connector_secret("k8s:holon/holon-connector-acme/PASSWORD", tenant_id="acme") == "v"
    assert seen["k8s"] == ("holon", "holon-connector-acme", "PASSWORD")
    assert resolve_connector_secret("aws:connectors/acme/db|password", tenant_id="acme") == "v"
    assert seen["aws"] == ("connectors/acme/db", "password")


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


def test_assert_http_url_resolve_false_skips_dns_but_blocks_literals() -> None:
    assert_http_url("https://idp.example.com/token", resolve=False)
    assert_http_url("http://this-host-does-not-exist.invalid/x", resolve=False)
    with pytest.raises(ConnectorSafetyError):
        assert_http_url("http://127.0.0.1/secret", resolve=False)
    with pytest.raises(ConnectorSafetyError):
        assert_http_url("http://identity/internal", resolve=False)


def test_inline_secret_allowed_outside_production(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    assert_no_inline_connector_secret("super-secret", field="password")


def test_inline_secret_blocked_in_production(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_ENV", "production")
    with pytest.raises(ConnectorSafetyError, match="secret_ref"):
        assert_no_inline_connector_secret("super-secret", field="password")
    assert_no_inline_connector_secret(None, field="password")
    assert_no_inline_connector_secret("  ", field="password")


def test_production_requires_secret_ref_on_create(monkeypatch) -> None:
    monkeypatch.setenv("HOLON_ENV", "production")
    with pytest.raises(ConnectorSafetyError, match="secret_ref is required"):
        assert_production_requires_secret_ref(None, is_update=False)
    with pytest.raises(ConnectorSafetyError, match="secret_ref is required"):
        assert_production_requires_secret_ref("  ", is_update=False)
    assert_production_requires_secret_ref("env:HOLON_CONN_ACME__ERP_PASSWORD", is_update=False)
    assert_production_requires_secret_ref(None, is_update=True)


def test_production_secret_ref_not_required_outside_production(monkeypatch) -> None:
    monkeypatch.delenv("HOLON_ENV", raising=False)
    assert_production_requires_secret_ref(None, is_update=False)


def test_destination_change_requires_secret() -> None:
    assert_destination_change_requires_secret(
        is_update=True, destination_changed=False, secret_provided=False
    )
    assert_destination_change_requires_secret(
        is_update=True, destination_changed=True, secret_provided=True
    )
    assert_destination_change_requires_secret(
        is_update=False, destination_changed=True, secret_provided=False
    )
    with pytest.raises(ConnectorSafetyError, match="destination changed"):
        assert_destination_change_requires_secret(
            is_update=True, destination_changed=True, secret_provided=False
        )


def test_unwrap_mapped_ip() -> None:
    # is_loopback on the wrapped form itself is Python-version-dependent
    # (differs between 3.9 and 3.11+) — what actually matters is that
    # assert_connector_host blocks it either way, via _unwrap_ip.
    with pytest.raises(ConnectorSafetyError):
        assert_connector_host("::ffff:127.0.0.1")
