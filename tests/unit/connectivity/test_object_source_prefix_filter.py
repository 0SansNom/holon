"""Unit tests for the object storage connector's key_prefix format filter.

Mirrors the SFTP connector's `_format_suffix` filtering: listing under
`key_prefix` must only read files whose name matches the source's
declared format, so Spark/Hive `_SUCCESS` markers and `.crc` sidecar
files under a parquet prefix don't break the sync.

PyArrow is stubbed before import: macOS host pytest can SIGBUS on real
pyarrow/numpy (CI uses Linux containers where the real import is fine).
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

REPO = Path(__file__).resolve().parents[3]


def _install_pyarrow_stubs() -> MagicMock:
    pafs = MagicMock()
    pafs.GcsFileSystem = MagicMock(name="GcsFileSystem")
    pafs.AzureFileSystem = MagicMock(name="AzureFileSystem")
    pafs.S3FileSystem = MagicMock(name="S3FileSystem")
    pafs.FileSelector = MagicMock(name="FileSelector")
    pafs.FileType = MagicMock(name="FileType")
    pafs.FileType.File = object()
    pafs.FileType.Directory = object()
    pafs.FileSystem = MagicMock(name="FileSystem")

    for name in ("pyarrow", "pyarrow.csv", "pyarrow.fs", "pyarrow.json", "pyarrow.parquet", "pyarrow.lib"):
        if name not in sys.modules:
            mod = ModuleType(name)
            sys.modules[name] = mod
    sys.modules["pyarrow.fs"].FileSystem = pafs.FileSystem  # type: ignore[attr-defined]
    sys.modules["pyarrow.fs"].FileSelector = pafs.FileSelector  # type: ignore[attr-defined]
    sys.modules["pyarrow.fs"].FileType = pafs.FileType  # type: ignore[attr-defined]
    sys.modules["pyarrow.fs"].S3FileSystem = pafs.S3FileSystem  # type: ignore[attr-defined]
    sys.modules["pyarrow.fs"].AzureFileSystem = pafs.AzureFileSystem  # type: ignore[attr-defined]
    sys.modules["pyarrow.fs"].GcsFileSystem = pafs.GcsFileSystem  # type: ignore[attr-defined]
    sys.modules["pyarrow.lib"].ArrowException = Exception  # type: ignore[attr-defined]
    return pafs


_install_pyarrow_stubs()
sys.modules.setdefault("asyncpg", MagicMock())
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "connectivity"))

from app import object_source_registry as osr  # noqa: E402


def test_format_suffix_matches_sftp_registry_mapping() -> None:
    assert osr._format_suffix("csv") == ".csv"
    assert osr._format_suffix("ndjson") == ".ndjson"
    assert osr._format_suffix("parquet") == ".parquet"


def _file_info(path: str, *, is_dir: bool = False) -> SimpleNamespace:
    file_type = osr.pafs.FileType.Directory if is_dir else osr.pafs.FileType.File
    return SimpleNamespace(path=path, type=file_type)


def test_fetch_sync_key_prefix_skips_spark_success_marker_and_crc() -> None:
    fake_fs = MagicMock()
    fake_fs.get_file_info.return_value = [
        _file_info("bucket/landing/part-00000.snappy.parquet"),
        _file_info("bucket/landing/part-00001.snappy.parquet"),
        _file_info("bucket/landing/_SUCCESS"),
        _file_info("bucket/landing/.part-00000.snappy.parquet.crc"),
        _file_info("bucket/landing/nested", is_dir=True),
    ]

    read_calls: list[str] = []

    def _fake_read_table(fs: object, path: str, format: str) -> MagicMock:
        read_calls.append(path)
        table = MagicMock()
        table.to_pylist.return_value = [{"path": path}]
        return table

    with (
        patch.object(osr, "_build_filesystem", return_value=fake_fs),
        patch.object(osr, "_read_table", side_effect=_fake_read_table),
    ):
        rows, cursor = osr._fetch_sync(
            kind="s3",
            endpoint="http://localhost:9000",
            access_key_id="minioadmin",
            secret_access_key="minioadmin",
            region="us-east-1",
            path_style=True,
            bucket="bucket",
            object_key=None,
            key_prefix="landing",
            format="parquet",
            incremental=False,
            last_synced_key=None,
        )

    assert read_calls == [
        "bucket/landing/part-00000.snappy.parquet",
        "bucket/landing/part-00001.snappy.parquet",
    ]
    assert rows == [
        {"path": "bucket/landing/part-00000.snappy.parquet"},
        {"path": "bucket/landing/part-00001.snappy.parquet"},
    ]
    assert cursor is None


def test_fetch_sync_key_prefix_incremental_still_filters_by_format() -> None:
    fake_fs = MagicMock()
    fake_fs.get_file_info.return_value = [
        _file_info("bucket/landing/2024-01-01.ndjson"),
        _file_info("bucket/landing/2024-01-02.ndjson"),
        _file_info("bucket/landing/_SUCCESS"),
    ]

    def _fake_read_table(fs: object, path: str, format: str) -> MagicMock:
        table = MagicMock()
        table.to_pylist.return_value = [{"path": path}]
        return table

    with (
        patch.object(osr, "_build_filesystem", return_value=fake_fs),
        patch.object(osr, "_read_table", side_effect=_fake_read_table),
    ):
        rows, cursor = osr._fetch_sync(
            kind="s3",
            endpoint="http://localhost:9000",
            access_key_id="minioadmin",
            secret_access_key="minioadmin",
            region="us-east-1",
            path_style=True,
            bucket="bucket",
            object_key=None,
            key_prefix="landing",
            format="ndjson",
            incremental=True,
            last_synced_key="landing/2024-01-01.ndjson",
        )

    assert rows == [{"path": "bucket/landing/2024-01-02.ndjson"}]
    assert cursor == "landing/2024-01-02.ndjson"
