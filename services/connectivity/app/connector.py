"""PostgreSQL connector — core-circle PostgreSQL source.

Read-only, always. A connector MUST NEVER write back to its source;
the only mutation path into a source system is a governed ontology
Action — Knowledge's own operational state, not this connector.

Two tables from the same source system, two explicit functions rather
than one generic "read a table" abstraction — there are exactly two of
them, and a real connector's field list per table is never actually
uniform enough to templatize cheaply.
"""

from __future__ import annotations

import asyncpg

CUSTOMERS_QUERY = """
    SELECT id, name, email, country, segment, lifetime_value, updated_at
    FROM customers
    ORDER BY id
"""

ORDERS_QUERY = """
    SELECT id, customer_id, product, amount, status, ordered_at
    FROM orders
    ORDER BY id
"""


async def _fetch(source_dsn: str, query: str) -> list[dict]:
    conn = await asyncpg.connect(source_dsn)
    try:
        rows = await conn.fetch(query)
    finally:
        await conn.close()
    return [dict(row) for row in rows]


async def read_customers(source_dsn: str) -> list[dict]:
    return await _fetch(source_dsn, CUSTOMERS_QUERY)


async def read_orders(source_dsn: str) -> list[dict]:
    return await _fetch(source_dsn, ORDERS_QUERY)
