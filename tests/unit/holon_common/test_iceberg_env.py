"""Unit tests for shared Iceberg env config helpers."""

from __future__ import annotations

import pytest

from holon_common.iceberg_env import iceberg_catalog_config_from_env, iceberg_kwargs


@pytest.fixture()
def iceberg_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOLON_ICEBERG_CATALOG_URI", "http://catalog:8181")
    monkeypatch.setenv("HOLON_ICEBERG_WAREHOUSE", "s3://warehouse")
    monkeypatch.setenv("HOLON_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "ak")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "sk")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")


def test_iceberg_catalog_config_from_env(iceberg_env: None) -> None:
    cfg = iceberg_catalog_config_from_env()
    assert cfg["catalog_uri"] == "http://catalog:8181"
    assert cfg["warehouse"] == "s3://warehouse"
    assert cfg["s3_endpoint"] == "http://minio:9000"
    assert cfg["access_key"] == "ak"
    assert cfg["secret_key"] == "sk"
    assert cfg["region"] == "eu-west-1"
    assert "tenant_id" not in cfg


def test_iceberg_kwargs_adds_tenant(iceberg_env: None) -> None:
    kwargs = iceberg_kwargs("acme")
    assert kwargs["tenant_id"] == "acme"
    assert kwargs["catalog_uri"] == "http://catalog:8181"


def test_iceberg_kwargs_accepts_explicit_config() -> None:
    kwargs = iceberg_kwargs("acme", config={"catalog_uri": "x", "warehouse": "y"})
    assert kwargs == {"catalog_uri": "x", "warehouse": "y", "tenant_id": "acme"}


def test_iceberg_catalog_config_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "HOLON_ICEBERG_CATALOG_URI",
        "HOLON_ICEBERG_WAREHOUSE",
        "HOLON_S3_ENDPOINT",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_REGION",
    ):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(KeyError):
        iceberg_catalog_config_from_env()
