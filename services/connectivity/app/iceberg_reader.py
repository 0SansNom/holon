"""Raw zone reader — reads Apache Iceberg tables from REST catalog."""

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
    """Read all rows from an Iceberg table in the raw namespace."""
    catalog = _load_catalog(catalog_uri, warehouse, s3_endpoint, access_key, secret_key, region)
    for attempt in range(1, _LOAD_TABLE_RETRIES + 1):
        try:
            table = catalog.load_table((NAMESPACE, table_name))
            break
        except NoSuchTableError:
            # Fail fast if table does not exist
            raise
        except Exception:
            if attempt == _LOAD_TABLE_RETRIES:
                raise
            time.sleep(_LOAD_TABLE_RETRY_DELAY_SECONDS)
    rows = table.scan().to_arrow().to_pylist()
    return [{key: _json_safe(value) for key, value in row.items()} for row in rows]
