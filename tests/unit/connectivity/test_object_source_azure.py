"""Unit tests for the Azure Blob branch of the object storage connector.

Live read/list behavior against S3 is already covered end-to-end in
tests/integration/connectivity/test_object_source_connector.py against a
real MinIO fixture; there is no Azurite fixture in the compose stack yet,
so these exercise config validation and filesystem selection only.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app.object_source_registry import (  # noqa: E402
    SourceConfigError,
    _build_filesystem,
    _default_azure_endpoint,
)


def test_default_azure_endpoint_is_the_public_blob_endpoint() -> None:
    assert _default_azure_endpoint("mystorageacct") == "https://mystorageacct.blob.core.windows.net"


def test_build_filesystem_azure_uses_account_name_and_key() -> None:
    with patch("app.object_source_registry.pafs.AzureFileSystem") as azure_fs:
        _build_filesystem(
            kind="azure",
            endpoint="https://mystorageacct.blob.core.windows.net",
            access_key_id="mystorageacct",
            secret_access_key="k3y",
            region="us-east-1",
            path_style=True,
        )
    azure_fs.assert_called_once_with(account_name="mystorageacct", account_key="k3y")


def test_build_filesystem_s3_is_unaffected() -> None:
    with patch("app.object_source_registry.pafs.S3FileSystem") as s3_fs:
        _build_filesystem(
            kind="s3",
            endpoint="http://localhost:9000",
            access_key_id="minioadmin",
            secret_access_key="minioadmin",
            region="us-east-1",
            path_style=True,
        )
    assert s3_fs.called


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
                kind="gcs",
            )
        )


def test_register_connection_requires_endpoint_for_s3() -> None:
    from app.object_source_registry import register_connection

    with pytest.raises(SourceConfigError, match="endpoint is required"):
        asyncio.run(
            register_connection(
                MagicMock(),
                tenant_id="t1",
                name="conn",
                access_key_id="acct",
                created_by_urn="urn:jdoe",
                kind="s3",
            )
        )
