"""Unit tests for the GCS branch of the object storage connector.

Auth model matches Foundry "JSON credentials" / CData
AuthScheme=OAuthJWT + OAuthJWTCertType=GOOGLEJSON: Project Id + service
account JSON. Live GCS is not in the compose stack — these cover config
validation and filesystem selection (mocked), same pattern as Azure.

PyArrow is stubbed before import: macOS host pytest can SIGBUS on real
pyarrow/numpy (CI uses Linux containers where the real import is fine).
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[3]


def _install_pyarrow_stubs() -> MagicMock:
    """Replace pyarrow packages with MagicMocks before importing the registry."""
    pafs = MagicMock()
    pafs.GcsFileSystem = MagicMock(name="GcsFileSystem")
    pafs.AzureFileSystem = MagicMock(name="AzureFileSystem")
    pafs.S3FileSystem = MagicMock(name="S3FileSystem")
    pafs.FileSelector = MagicMock(name="FileSelector")
    pafs.FileType = MagicMock(name="FileType")
    pafs.FileSystem = MagicMock(name="FileSystem")

    for name in ("pyarrow", "pyarrow.csv", "pyarrow.fs", "pyarrow.json", "pyarrow.parquet", "pyarrow.lib"):
        if name not in sys.modules:
            mod = ModuleType(name)
            sys.modules[name] = mod
    sys.modules["pyarrow.fs"] = pafs  # type: ignore[assignment]
    sys.modules["pyarrow.csv"] = MagicMock()
    sys.modules["pyarrow.json"] = MagicMock()
    sys.modules["pyarrow.parquet"] = MagicMock()
    lib = ModuleType("pyarrow.lib")
    lib.ArrowException = type("ArrowException", (Exception,), {})  # type: ignore[attr-defined]
    sys.modules["pyarrow.lib"] = lib
    return pafs


_PAFS = _install_pyarrow_stubs()

sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.object_source_registry import (  # noqa: E402
    SourceConfigError,
    _build_filesystem,
    _default_gcs_endpoint,
    _validate_gcs_service_account_json,
)

_SAMPLE_SA = json.dumps(
    {
        "type": "service_account",
        "project_id": "demo-project",
        "private_key_id": "abc",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----\n",
        "client_email": "demo@demo-project.iam.gserviceaccount.com",
        "client_id": "123",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
)


def test_default_gcs_endpoint_is_storage_googleapis() -> None:
    assert _default_gcs_endpoint() == "https://storage.googleapis.com"


def test_validate_gcs_json_accepts_service_account_shape() -> None:
    _validate_gcs_service_account_json(_SAMPLE_SA)


def test_validate_gcs_json_rejects_plain_password() -> None:
    with pytest.raises(SourceConfigError, match="service account JSON"):
        _validate_gcs_service_account_json("not-a-json-key")


def test_build_filesystem_gcs_mints_token_and_uses_gcs() -> None:
    fake_creds = MagicMock()
    fake_creds.token = "ya29.token"
    fake_creds.expiry = datetime.now(timezone.utc) + timedelta(hours=1)
    fake_creds.refresh = MagicMock()

    gcs_ctor = MagicMock(name="GcsFileSystem")
    with (
        patch("app.object_source_registry.pafs.GcsFileSystem", gcs_ctor),
        patch(
            "google.oauth2.service_account.Credentials.from_service_account_info",
            return_value=fake_creds,
        ) as from_info,
        patch("google.auth.transport.requests.Request"),
    ):
        _build_filesystem(
            kind="gcs",
            endpoint="https://storage.googleapis.com",
            access_key_id="demo-project",
            secret_access_key=_SAMPLE_SA,
            region="US",
            path_style=False,
        )
    from_info.assert_called_once()
    fake_creds.refresh.assert_called_once()
    gcs_ctor.assert_called_once()
    kwargs = gcs_ctor.call_args.kwargs
    assert kwargs["access_token"] == "ya29.token"
    assert kwargs["project_id"] == "demo-project"
    assert kwargs["default_bucket_location"] == "US"


def test_register_connection_defaults_gcs_endpoint_and_region() -> None:
    from app.object_source_registry import register_connection

    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()

    async def _get_connection(*_a, **_k):
        return {
            "name": "gcs1",
            "kind": "gcs",
            "endpoint": "https://storage.googleapis.com",
            "region": "US",
            "access_key_id": "demo-project",
            "has_secret_access_key": True,
        }

    with (
        patch("app.object_source_registry.assert_connector_host"),
        patch("app.object_source_registry.assert_connector_secret_ref"),
        patch("app.object_source_registry.assert_no_inline_connector_secret"),
        patch("app.object_source_registry.assert_production_requires_secret_ref"),
        patch("app.object_source_registry.get_connection", side_effect=_get_connection),
    ):
        result = asyncio.run(
            register_connection(
                pool,
                tenant_id="t1",
                name="gcs1",
                access_key_id="demo-project",
                created_by_urn="urn:jdoe",
                kind="gcs",
                secret_ref="env:GCS_SA_JSON",
            )
        )
    assert result["kind"] == "gcs"
    insert_args = pool.execute.call_args.args
    # VALUES ($1..$10): tenant, name, kind, endpoint, region, ...
    assert insert_args[4] == "https://storage.googleapis.com"
    assert insert_args[5] == "US"
    assert insert_args[9] is False  # path_style forced off for GCS


def test_register_connection_rejects_unknown_kind() -> None:
    from app.object_source_registry import register_connection

    with pytest.raises(SourceConfigError, match="kind must be one of"):
        asyncio.run(
            register_connection(
                MagicMock(),
                tenant_id="t1",
                name="conn",
                access_key_id="acct",
                created_by_urn="urn:jdoe",
                kind="ftp",
            )
        )
