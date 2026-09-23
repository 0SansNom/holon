"""Starter gold-set questions for the Intelligence evaluation harness."""

from __future__ import annotations

import logging

import asyncpg

logger = logging.getLogger("intelligence.gold_set")

# Seeded once when gold_set_question is empty. Categories cover ontology
# metadata RAG that Holon's Qdrant index actually contains.
STARTER_GOLD_SET: list[tuple[str, str, str | None]] = [
    ("What is a Customer in the ontology?", "ontology", "Customer"),
    ("Which object type represents an order?", "ontology", "Order"),
    ("What action puts a customer on credit hold?", "actions", "putOnCreditHold"),
    ("How do I look up customers?", "ontology", "Customer"),
    ("What is the relationship between customers and orders?", "ontology", "Order"),
    ("Explain the putOnCreditHold action.", "actions", "putOnCreditHold"),
    ("Which object types exist for sales?", "ontology", "Customer"),
    ("What glossary terms relate to customers?", "glossary", "Customer"),
    ("Can an agent put a customer on credit hold?", "actions", "putOnCreditHold"),
    ("Summarize the Customer object type.", "ontology", "Customer"),
    ("What properties might an Order have?", "ontology", "Order"),
    ("List actions available on Customer.", "actions", "Customer"),
]


def gold_set_disclaimer(*, seeded_starter: bool = True) -> str:
    if seeded_starter:
        return (
            "Starter gold set — Holon seeds a small ontology/actions question pack when "
            "gold_set_question is empty. Treat metrics as a smoke signal, not production "
            "eval quality; extend the set for your domain before comparing models."
        )
    return (
        "No gold set is seeded by default — this evaluates whatever rows currently "
        "exist in gold_set_question. Populate it yourself before treating these "
        "metrics as meaningful; an empty set trivially reports null accuracy."
    )


async def seed_starter_gold_set(conn: asyncpg.Connection) -> int:
    """Insert the starter gold set if `gold_set_question` is empty. Returns rows inserted."""
    count = await conn.fetchval("SELECT COUNT(*) FROM gold_set_question")
    if count and int(count) > 0:
        return 0
    inserted = 0
    for question_text, category, expected in STARTER_GOLD_SET:
        await conn.execute(
            "INSERT INTO gold_set_question (question_text, category, expected_urn_substring) "
            "VALUES ($1, $2, $3)",
            question_text,
            category,
            expected,
        )
        inserted += 1
    logger.info("seeded %d starter gold_set_question rows", inserted)
    return inserted
