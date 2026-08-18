"""PostgreSQL connector — core PostgreSQL source ingestion."""

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
