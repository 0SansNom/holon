"""Object and SFTP files are read as row batches, not one buffer."""

from __future__ import annotations

import asyncio
import importlib
import io
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "services" / "connectivity"))


def _load_batch_helpers():
    """Other connector tests install empty pyarrow stubs before import."""
    saved = {
        name: sys.modules.pop(name)
        for name in list(sys.modules)
        if (name == "pyarrow" or name.startswith("pyarrow.")) and getattr(sys.modules[name], "__file__", None) is None
    }
    try:
        module = importlib.import_module("app.file_batches")
        if saved:
            module = importlib.reload(module)
        return module.iter_file_batches, module.iter_in_thread
    finally:
        sys.modules.update(saved)


iter_file_batches, iter_in_thread = _load_batch_helpers()
from app.sftp_source_fetch import _SftpStream  # noqa: E402

pytestmark = pytest.mark.unit


class _Handle:
    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def read(self, size: int = -1) -> bytes:
        return self._buf.read(size)

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        return self._buf.seek(offset, whence)

    def tell(self) -> int:
        return self._buf.tell()


def test_csv_is_yielded_in_row_chunks() -> None:
    raw = b"id,name\n1,a\n2,b\n3,c\n"
    batches = list(iter_file_batches(io.BytesIO(raw), "csv", batch_rows=2))
    assert [len(batch) for batch in batches] == [2, 1]
    assert [(row["id"], row["name"]) for batch in batches for row in batch] == [(1, "a"), (2, "b"), (3, "c")]


def test_sftp_handle_is_readable_as_csv() -> None:
    raw = b"id\n1\n2\n"
    batches = list(iter_file_batches(_SftpStream(_Handle(raw)), "csv", batch_rows=1))
    assert [row["id"] for batch in batches for row in batch] == [1, 2]


def test_iter_in_thread_yields_in_order_and_raises() -> None:
    async def collect():
        def factory():
            yield [{"n": 1}]
            yield [{"n": 2}]

        return [batch async for batch in iter_in_thread(factory)]

    assert asyncio.run(collect()) == [[{"n": 1}], [{"n": 2}]]

    async def fail():
        def factory():
            yield [{"n": 1}]
            raise OSError("disk")

        return [batch async for batch in iter_in_thread(factory)]

    with pytest.raises(OSError, match="disk"):
        asyncio.run(fail())
