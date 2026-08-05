"""Business Glossary management.

Provides a business vocabulary (synonyms, abbreviations, domain terms)
so that entity resolution steps can resolve user wording ("grand compte", "encours")
onto the ontology's own names ("Customer", "lifetimeValue").

Seeded as real, specific terms tied to the domain.
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

# (term, definition, synonyms, related object type name or None)
_GLOSSARY_SEED = [
    (
        "client",
        "A business account that buys from us — see ObjectType Customer.",
        ["customer", "compte client"],
        "Customer",
    ),
    (
        "grand compte",
        "A Customer in the 'enterprise' commercial segment — our highest-value tier.",
        ["enterprise customer", "grand client"],
        "Customer",
    ),
    (
        "encours",
        "A Customer's lifetime value — total historical spend, in euros.",
        ["lifetime value", "valeur client"],
        "Customer",
    ),
    (
        "mise en attente de crédit",
        "The Customer.putOnCreditHold Action — blocks further orders pending payment resolution.",
        ["credit hold", "blocage crédit"],
        "Customer",
    ),
    (
        "clôture de compte",
        "The Customer.closeAccount Action — permanently closes an account. High-risk, requires approval.",
        ["account closure", "fermeture de compte"],
        "Customer",
    ),
    (
        "commande",
        "A single purchase placed by a Customer — see ObjectType Order.",
        ["order", "achat"],
        "Order",
    ),
    (
        "ticket",
        "A customer support request — see ObjectType SupportTicket.",
        ["support ticket", "demande d'assistance"],
        "SupportTicket",
    ),
    (
        "avis produit",
        "A public review left against an Order — see ObjectType ProductReview.",
        ["product review", "évaluation"],
        "ProductReview",
    ),
    (
        "fournisseur",
        "A vendor we source materials/components from — see ObjectType Supplier.",
        ["supplier", "vendeur"],
        "Supplier",
    ),
    (
        "niveau de stock",
        "The current on-hand quantity of a SKU at a warehouse — see ObjectType InventoryLevel.",
        ["inventory level", "stock disponible"],
        "InventoryLevel",
    ),
]


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def ensure_seeded(pool: asyncpg.Pool, tenant_id: str, workspace_id: str, object_type_urn_fn) -> None:
    for term, definition, synonyms, object_type_name in _GLOSSARY_SEED:
        related_urn = object_type_urn_fn(tenant_id, workspace_id, object_type_name) if object_type_name else None
        await pool.execute(
            """
            INSERT INTO business_glossary (tenant_id, term, definition, synonyms, related_object_type_urn)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (tenant_id, term) DO UPDATE SET
                definition = EXCLUDED.definition,
                synonyms = EXCLUDED.synonyms,
                related_object_type_urn = EXCLUDED.related_object_type_urn
            """,
            tenant_id,
            term,
            definition,
            synonyms,
            related_urn,
        )


async def list_terms(pool: asyncpg.Pool, tenant_id: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT term, definition, synonyms, related_object_type_urn FROM business_glossary "
        "WHERE tenant_id = $1 ORDER BY term",
        tenant_id,
    )
    return [dict(row) for row in rows]


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
