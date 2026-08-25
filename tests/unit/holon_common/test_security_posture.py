"""Tests for Security Posture."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))

from holon_common.security_posture import (  # noqa: E402
    ProductionSecurityError,
    assert_production_posture,
    is_production,
)


def _clear_posture_env(monkeypatch) -> None:
    for key in (
        "HOLON_ENV",
        "HOLON_METRICS_TOKEN",
        "HOLON_CORS_ORIGINS",
        "HOLON_MINTABLE_PRINCIPAL_URNS",
        "HOLON_ALLOW_USER_JWT_MINT",
        "HOLON_ALLOW_LOCAL_USER_MINT",
        "HOLON_SERVING_STORE_REQUIRE_MATERIALIZED",
        "HOLON_INTELLIGENCE_ENABLED",
        "HOLON_ALLOW_JOBLIB_MODELS",
        "HOLON_ALLOW_TOOL_PLUGIN_REGISTER",
        "HOLON_JWT_ALG",
        "HOLON_JWT_REQUIRE_ASYMMETRIC",
        "HOLON_JWT_PUBLIC_KEYS",
        "HOLON_JWT_PUBLIC_KEY",
    ):
        monkeypatch.delenv(key, raising=False)


def _base_prod(monkeypatch) -> None:
    _clear_posture_env(monkeypatch)
    monkeypatch.setenv("HOLON_ENV", "production")
    monkeypatch.setenv("HOLON_METRICS_TOKEN", "metrics-secret")
    monkeypatch.setenv("HOLON_CORS_ORIGINS", "https://holon.example.com")


def test_is_production(monkeypatch) -> None:
    _clear_posture_env(monkeypatch)
    assert is_production() is False
    monkeypatch.setenv("HOLON_ENV", "production")
    assert is_production() is True
    monkeypatch.setenv("HOLON_ENV", "prod")
    assert is_production() is True
    monkeypatch.setenv("HOLON_ENV", "dev")
    assert is_production() is False


def test_assert_production_posture_noop_outside_prod(monkeypatch) -> None:
    _clear_posture_env(monkeypatch)
    monkeypatch.setenv("HOLON_ENV", "dev")
    assert_production_posture(service_name="connectivity-platform")


def test_assert_production_posture_passes_identity(monkeypatch) -> None:
    _base_prod(monkeypatch)
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "")
    monkeypatch.setenv("HOLON_ALLOW_USER_JWT_MINT", "true")
    assert_production_posture(service_name="identity-platform")


def test_assert_production_posture_rejects_user_mint_on_non_identity(monkeypatch) -> None:
    _base_prod(monkeypatch)
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "ingest-bot")
    monkeypatch.setenv("HOLON_ALLOW_USER_JWT_MINT", "true")
    with pytest.raises(ProductionSecurityError, match="HOLON_ALLOW_USER_JWT_MINT"):
        assert_production_posture(service_name="intelligence-platform")


def test_assert_production_posture_knowledge_requires_materialized(monkeypatch) -> None:
    _base_prod(monkeypatch)
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "knowledge-model-caller")
    monkeypatch.setenv("HOLON_SERVING_STORE_REQUIRE_MATERIALIZED", "false")
    with pytest.raises(ProductionSecurityError, match="HOLON_SERVING_STORE_REQUIRE_MATERIALIZED"):
        assert_production_posture(service_name="knowledge-platform")


def test_assert_production_posture_rejects_enabled_intelligence(monkeypatch) -> None:
    _base_prod(monkeypatch)
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "intelligence-indexer")
    monkeypatch.setenv("HOLON_INTELLIGENCE_ENABLED", "true")
    with pytest.raises(ProductionSecurityError, match="HOLON_INTELLIGENCE_ENABLED"):
        assert_production_posture(service_name="intelligence-platform")


def test_assert_production_posture_passes_intelligence_when_disabled(monkeypatch) -> None:
    _base_prod(monkeypatch)
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "intelligence-indexer")
    monkeypatch.setenv("HOLON_INTELLIGENCE_ENABLED", "false")
    assert_production_posture(service_name="intelligence-platform")


def test_assert_production_posture_rejects_joblib_and_tool_plugin_flags(monkeypatch) -> None:
    _base_prod(monkeypatch)
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "intelligence-indexer")
    monkeypatch.setenv("HOLON_INTELLIGENCE_ENABLED", "false")
    monkeypatch.setenv("HOLON_ALLOW_JOBLIB_MODELS", "true")
    with pytest.raises(ProductionSecurityError, match="HOLON_ALLOW_JOBLIB_MODELS"):
        assert_production_posture(service_name="intelligence-platform")
    monkeypatch.setenv("HOLON_ALLOW_JOBLIB_MODELS", "false")
    monkeypatch.setenv("HOLON_ALLOW_TOOL_PLUGIN_REGISTER", "true")
    with pytest.raises(ProductionSecurityError, match="HOLON_ALLOW_TOOL_PLUGIN_REGISTER"):
        assert_production_posture(service_name="intelligence-platform")


def test_assert_production_posture_require_asymmetric(monkeypatch) -> None:
    _base_prod(monkeypatch)
    monkeypatch.setenv("HOLON_MINTABLE_PRINCIPAL_URNS", "")
    monkeypatch.setenv("HOLON_ALLOW_USER_JWT_MINT", "true")
    monkeypatch.setenv("HOLON_JWT_REQUIRE_ASYMMETRIC", "true")
    monkeypatch.setenv("HOLON_JWT_ALG", "HS256")
    with pytest.raises(ProductionSecurityError, match="HOLON_JWT_REQUIRE_ASYMMETRIC"):
        assert_production_posture(service_name="identity-platform")
    monkeypatch.setenv("HOLON_JWT_ALG", "RS256")
    with pytest.raises(ProductionSecurityError, match="HOLON_JWT_PUBLIC_KEYS"):
        assert_production_posture(service_name="identity-platform")
    monkeypatch.setenv("HOLON_JWT_PUBLIC_KEYS", '{"k1":"-----BEGIN PUBLIC KEY-----\\nMIIB\\n-----END PUBLIC KEY-----"}')
    assert_production_posture(service_name="identity-platform")
