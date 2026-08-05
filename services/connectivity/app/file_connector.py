"""The file import connector — "Import fichiers" Connectivity capability.

A batch pull of a static file drop, not a database/API client:
`pyarrow.fs.S3FileSystem` reads the object's bytes directly from MinIO
(already a transitive dependency via `pyiceberg[pyarrow,s3fs]` — no new
package), then stdlib `csv` parses it. A fourth distinct concurrency
shape: a synchronous filesystem client, wrapped in `asyncio.to_thread` at
the call site (see `main.py`'s `DATASET_READERS["suppliers"]`) — contrast
with `connector.py` (asyncpg, natively async), `mongo_connector.py`
(pymongo, sync, thread-wrapped), and `rest_connector.py` (httpx, natively
async).
"""

from __future__ import annotations

import csv
import io

import pyarrow.fs as pafs


def read_suppliers_csv(
    *,
    s3_endpoint: str,
    access_key: str,
    secret_key: str,
    region: str,
    bucket_path: str = "holon-warehouse/landing/suppliers.csv",
) -> list[dict]:
    endpoint_override = s3_endpoint.split("://", 1)[-1]
    fs = pafs.S3FileSystem(
        access_key=access_key,
        secret_key=secret_key,
        endpoint_override=endpoint_override,
        scheme="http",
        region=region,
    )
    with fs.open_input_stream(bucket_path) as f:
        raw = f.readall().decode("utf-8")

    reader = csv.DictReader(io.StringIO(raw))
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "country": row["country"],
            "category": row["category"],
        }
        for row in reader
    ]
