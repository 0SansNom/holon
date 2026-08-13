"""Business Glossary management.

Provides a business vocabulary (synonyms, abbreviations, domain terms)
so that entity resolution steps can resolve user wording onto the
ontology's own names.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

DDL = """
CREATE TABLE IF NOT EXISTS business_glossary (
    tenant_id TEXT NOT NULL,
    term TEXT NOT NULL,
    definition TEXT NOT NULL,
    synonyms TEXT[] NOT NULL DEFAULT '{}',
    related_object_type_urn TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, term)
);
"""


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def list_terms(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT term, definition, synonyms, related_object_type_urn FROM business_glossary "
        "WHERE tenant_id = $1 ORDER BY term",
        tenant_id,
    )
    return [dict(row) for row in rows]


async def create_term(
    pool: asyncpg.Pool,
    *,
    tenant_id: str,
    term: str,
    definition: str,
    synonyms: list[str],
    related_object_type_urn: Optional[str],
) -> dict:
    await pool.execute(
        """
        INSERT INTO business_glossary (tenant_id, term, definition, synonyms, related_object_type_urn)
        VALUES ($1, $2, $3, $4, $5)
        """,
        tenant_id,
        term,
        definition,
        synonyms,
        related_object_type_urn,
    )
    return await get_term(pool, tenant_id, term)


async def get_term(pool: asyncpg.Pool, tenant_id: str, term: str) -> Optional[dict]:
    """Case-insensitive, and matches a synonym as well as the canonical
    term — the whole point of a glossary is resolving whatever wording a
    user actually typed, not just the exact term string.
    """
    row = await pool.fetchrow(
        """
        SELECT term, definition, synonyms, related_object_type_urn FROM business_glossary
        WHERE tenant_id = $1 AND (lower(term) = lower($2) OR lower($2) = ANY(SELECT lower(s) FROM unnest(synonyms) s))
        """,
        tenant_id,
        term,
    )
    return dict(row) if row else None
