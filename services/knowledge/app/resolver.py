"""Instance resolution.

`serving_store.py` materializes every row into Postgres once per sync, and
`main.py`'s `_resolve_one`/`_resolve_many` read from there. This module's
two remaining jobs are (1) feeding that materialization from
`catalog._catalogue_sync`, once per sync, and (2) the federated
fallback when the serving store has no entry yet for a key — a live,
query-on-read scan is still the right thing to do in both cases, just no
longer on every request.

DuckDB is the read engine: Iceberg's Arrow scan feeds a DuckDB
in-memory relation, which is the same execution path formalizes
behind an Execution Plan.
"""

from __future__ import annotations

import time
from typing import Optional

import duckdb
from pyiceberg.catalog import load_catalog

_LOAD_TABLE_RETRIES = 4
_LOAD_TABLE_RETRY_DELAY_SECONDS = 1.5


def _load_table(table_name: str, *, catalog_uri: str, warehouse: str, s3_endpoint: str, access_key: str, secret_key: str, region: str):
    catalog = load_catalog(
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
    for attempt in range(1, _LOAD_TABLE_RETRIES + 1):
        try:
            return catalog.load_table(("raw", table_name))
        except Exception:
            if attempt == _LOAD_TABLE_RETRIES:
                raise
            time.sleep(_LOAD_TABLE_RETRY_DELAY_SECONDS)


def scan_at(table_name: str, *, snapshot_id: Optional[int] = None, **iceberg_config):
    """Replay a query pinned to a *historical* Iceberg snapshot,
    not whatever the table's current state happens to be. `snapshot_id`
    omitted means "current", the behavior every `fetch_*` function already
    relies on; every retained snapshot is otherwise independently
    queryable, which is exactly what a frozen ExecutionPlan needs to be
    genuinely replayable (`execution.replay`).
    """
    table = _load_table(table_name, **iceberg_config)
    return table.scan(snapshot_id=snapshot_id) if snapshot_id is not None else table.scan()


def fetch_customers(*, customer_id: Optional[int] = None, **iceberg_config) -> list[dict]:
    table = _load_table("customers", **iceberg_config)
    arrow_table = table.scan().to_arrow()

    con = duckdb.connect()
    con.register("customers", arrow_table)
    if customer_id is not None:
        rows = con.execute("SELECT * FROM customers WHERE id = ?", [customer_id]).fetch_arrow_table()
    else:
        rows = con.execute("SELECT * FROM customers ORDER BY id").fetch_arrow_table()
    return rows.to_pylist()


def fetch_orders(*, order_id: Optional[int] = None, customer_id: Optional[int] = None, **iceberg_config) -> list[dict]:
    """`customer_id` is the relation traversal case — Order.customerId -> Customer.id, per
    `ontology.RELATION_TYPES`.
    """
    table = _load_table("orders", **iceberg_config)
    arrow_table = table.scan().to_arrow()

    con = duckdb.connect()
    con.register("orders", arrow_table)
    if order_id is not None:
        rows = con.execute("SELECT * FROM orders WHERE id = ?", [order_id]).fetch_arrow_table()
    elif customer_id is not None:
        rows = con.execute("SELECT * FROM orders WHERE customer_id = ? ORDER BY id", [customer_id]).fetch_arrow_table()
    else:
        rows = con.execute("SELECT * FROM orders ORDER BY id").fetch_arrow_table()
    return rows.to_pylist()


def fetch_support_tickets(*, ticket_id: Optional[int] = None, customer_id: Optional[int] = None, **iceberg_config) -> list[dict]:
    """Same shape as `fetch_orders` — a second relation, `SupportTicket.customer`,
    traversed the same way (`ontology.RELATION_TYPES`).
    """
    table = _load_table("support_tickets", **iceberg_config)
    arrow_table = table.scan().to_arrow()

    con = duckdb.connect()
    con.register("support_tickets", arrow_table)
    if ticket_id is not None:
        rows = con.execute("SELECT * FROM support_tickets WHERE id = ?", [ticket_id]).fetch_arrow_table()
    elif customer_id is not None:
        rows = con.execute(
            "SELECT * FROM support_tickets WHERE customer_id = ? ORDER BY id", [customer_id]
        ).fetch_arrow_table()
    else:
        rows = con.execute("SELECT * FROM support_tickets ORDER BY id").fetch_arrow_table()
    return rows.to_pylist()


def fetch_reviews(*, review_id: Optional[int] = None, order_id: Optional[int] = None, **iceberg_config) -> list[dict]:
    """Third relation, `ProductReview.order` — the first traversal target
    that isn't Customer (`ontology.RELATION_TYPES`).
    """
    table = _load_table("reviews", **iceberg_config)
    arrow_table = table.scan().to_arrow()

    con = duckdb.connect()
    con.register("reviews", arrow_table)
    if review_id is not None:
        rows = con.execute("SELECT * FROM reviews WHERE id = ?", [review_id]).fetch_arrow_table()
    elif order_id is not None:
        rows = con.execute("SELECT * FROM reviews WHERE order_id = ? ORDER BY id", [order_id]).fetch_arrow_table()
    else:
        rows = con.execute("SELECT * FROM reviews ORDER BY id").fetch_arrow_table()
    return rows.to_pylist()


def fetch_suppliers(*, supplier_id: Optional[int] = None, **iceberg_config) -> list[dict]:
    """Fourth connector's dataset (file import) — standalone, no
    relation traversal into it, same shape as `fetch_customers` otherwise.
    """
    table = _load_table("suppliers", **iceberg_config)
    arrow_table = table.scan().to_arrow()

    con = duckdb.connect()
    con.register("suppliers", arrow_table)
    if supplier_id is not None:
        rows = con.execute("SELECT * FROM suppliers WHERE id = ?", [supplier_id]).fetch_arrow_table()
    else:
        rows = con.execute("SELECT * FROM suppliers ORDER BY id").fetch_arrow_table()
    return rows.to_pylist()


def fetch_inventory_levels(*, sku: Optional[str] = None, **iceberg_config) -> list[dict]:
    """Fifth connector's dataset (streaming) — `id` holds the SKU
    string directly (see `stream_connector.py`), the first ObjectType in
    this build keyed by something other than an integer.
    """
    table = _load_table("inventory_levels", **iceberg_config)
    arrow_table = table.scan().to_arrow()

    con = duckdb.connect()
    con.register("inventory_levels", arrow_table)
    if sku is not None:
        rows = con.execute("SELECT * FROM inventory_levels WHERE id = ?", [sku]).fetch_arrow_table()
    else:
        rows = con.execute("SELECT * FROM inventory_levels ORDER BY id").fetch_arrow_table()
    return rows.to_pylist()
