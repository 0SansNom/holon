"""Raw zone reader — the read-side counterpart to `iceberg_writer.py`,
needed for  (Pipeline/Transform DAG): a TransformStep's input is
an *existing* Iceberg table, something Connectivity has never needed to
read before (every core connector only ever writes). `resolver.py` in
Knowledge already has this exact "load a pyiceberg REST-catalog table,
scan it" logic per-ObjectType; this is the same connection shape, kept
in Connectivity rather than imported cross-service (each service owns
its own Iceberg client the same way each already owns its own copy of
this catalog-loading boilerplate — `iceberg_writer._load_catalog` here
and `resolver._load_table` in Knowledge are already two independent
copies of the same few lines, not something this introduces new).
"""

from __future__ import annotations

import datetime
import decimal
import time

from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import NoSuchTableError

_LOAD_TABLE_RETRIES = 4
_LOAD_TABLE_RETRY_DELAY_SECONDS = 1.5

NAMESPACE = "raw"


def _json_safe(value):
    """Normalizes PyArrow/Iceberg values to JSON-serializable types.
    Arrow's `to_pylist()` returns `decimal.Decimal` for decimal columns
    and `datetime`/`date` objects for timestamp columns. When rows are
    transmitted via standard JSON serializers (e.g. `httpx` or `json.dumps`
    in pipeline steps), these types require explicit conversion to `float`
    or ISO strings.
    """
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    return value


def _load_catalog(catalog_uri: str, warehouse: str, s3_endpoint: str, access_key: str, secret_key: str, region: str):
    return load_catalog(
        "holon",
        **{
            "type": "rest",
            "uri": catalog_uri,
            "warehouse": warehouse,
            "s3.endpoint": s3_endpoint,
            "s3.access-key-id": access_key,
            "s3.secret-access-key": secret_key,
            "s3.region": region,
            "s3.path-style-access": "true",
        },
    )


def read_table(
    table_name: str,
    *,
    catalog_uri: str,
    warehouse: str,
    s3_endpoint: str,
    access_key: str,
    secret_key: str,
    region: str,
) -> list[dict]:
    """Any named table in the `raw` namespace — unlike `resolver.py`'s
    per-ObjectType `fetch_*` functions, a pipeline step's input can be
    *any* previously-synced dataset (a core connector's, a plugin's, or
    an earlier step's own output), so this stays generic rather than
    growing one function per name.
    """
    catalog = _load_catalog(catalog_uri, warehouse, s3_endpoint, access_key, secret_key, region)
    for attempt in range(1, _LOAD_TABLE_RETRIES + 1):
        try:
            table = catalog.load_table((NAMESPACE, table_name))
            break
        except NoSuchTableError:
            # A real "this dataset was never synced" case, not a transient
            # write-contention blip — retrying can't fix a table that
            # doesn't exist, so this fails fast rather than burning
            # `_LOAD_TABLE_RETRIES * _LOAD_TABLE_RETRY_DELAY_SECONDS`
            # before giving the same answer.
            raise
        except Exception:
            if attempt == _LOAD_TABLE_RETRIES:
                raise
            time.sleep(_LOAD_TABLE_RETRY_DELAY_SECONDS)
    rows = table.scan().to_arrow().to_pylist()
    return [{key: _json_safe(value) for key, value in row.items()} for row in rows]
