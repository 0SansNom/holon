"""Turn a file into row batches without holding the whole file."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from typing import AsyncIterator, Callable, Iterator

import pyarrow.csv as pacsv
import pyarrow.json as pajson
import pyarrow.parquet as papq

_FILE_BATCH_ROWS = 50_000
_BLOCK_BYTES = 1 << 20


def _chunks(rows: list[dict], size: int) -> Iterator[list[dict]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def iter_file_batches(readable, format: str, *, batch_rows: int = _FILE_BATCH_ROWS) -> Iterator[list[dict]]:
    """Yield row batches from a binary file object. `readable` stays open for the caller."""
    if format == "parquet":
        for batch in papq.ParquetFile(readable).iter_batches(batch_size=batch_rows):
            rows = batch.to_pylist()
            if rows:
                yield rows
        return
    if format == "csv":
        reader = pacsv.open_csv(readable, read_options=pacsv.ReadOptions(block_size=_BLOCK_BYTES))
    elif format == "ndjson":
        reader = pajson.open_json(readable, read_options=pajson.ReadOptions(block_size=_BLOCK_BYTES))
    else:
        raise ValueError(f"unsupported file format {format!r}")
    for batch in reader:
        yield from _chunks(batch.to_pylist(), batch_rows)


async def iter_in_thread(factory: Callable[[], Iterator[list[dict]]]) -> AsyncIterator[list[dict]]:
    """Run a sync generator on one thread and yield its batches.

    File handles and remote clients are not safe to share across threads,
    so the whole generator stays on the worker that created it.
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=1)
    stop = threading.Event()

    def _put(item: tuple) -> bool:
        pending = asyncio.run_coroutine_threadsafe(queue.put(item), loop)
        while not stop.is_set():
            try:
                pending.result(timeout=0.2)
                return True
            except FutureTimeout:
                continue
        pending.cancel()
        return False

    def pump() -> None:
        gen = factory()
        try:
            for batch in gen:
                if not _put(("batch", batch)):
                    return
        except Exception as exc:
            _put(("err", exc))
            return
        finally:
            gen.close()
        _put(("end", None))

    worker = asyncio.create_task(asyncio.to_thread(pump))
    try:
        while True:
            kind, payload = await queue.get()
            if kind == "end":
                break
            if kind == "err":
                raise payload
            yield payload
    finally:
        stop.set()
        await worker
