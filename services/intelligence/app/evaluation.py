"""Evaluation harness.

Provides a repeatable evaluation run against LLM models, computing
measurable metrics (latency, token count, accuracy) and security checks.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import asyncpg
import httpx
from qdrant_client import AsyncQdrantClient

from .context_builder import ask as context_builder_ask
from .embeddings import EmbeddingClient
from .llm_gateway import LLMClient

logger = logging.getLogger("intelligence.evaluation")

DDL = """
CREATE TABLE IF NOT EXISTS gold_set_question (
    id BIGSERIAL PRIMARY KEY,
    question_text TEXT NOT NULL,
    category TEXT NOT NULL,
    expected_urn_substring TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval_run (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    metrics JSONB
);
"""

# A small, engineering-authored starter set — see module docstring's
# scoping note. (question_text, category, expected_urn_substring)
STARTER_GOLD_SET = [
    ("Tell me about customer 1", "lookup", "Customer/1"),
    ("Tell me about customer 4", "lookup", "Customer/4"),
    ("What orders does customer 1 have?", "traversal", "Order"),
    ("How many Order records have status pending?", "aggregation", "plan:"),
    ("How many Order records have status delivered?", "aggregation", "plan:"),
    ("How many SupportTicket records have status open?", "aggregation", "plan:"),
    ("What does grand compte mean?", "semantic", "glossary:"),
    ("What does encours mean?", "semantic", "glossary:encours"),
    ("What is a ticket in this system?", "semantic", "glossary:ticket"),
    ("Tell me about supplier 1", "lookup", "Supplier/1"),
    ("Tell me about order 1", "lookup", "Order/1"),
    ("What is a fournisseur?", "semantic", "glossary:fournisseur"),
    ("Tell me about customer 2", "lookup", "Customer/2"),
    ("Tell me about customer 8", "lookup", "Customer/8"),
]


async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


async def ensure_seeded(pool: asyncpg.Pool) -> None:
    existing = await pool.fetchval("SELECT COUNT(*) FROM gold_set_question")
    if existing:
        return
    for question_text, category, expected in STARTER_GOLD_SET:
        await pool.execute(
            "INSERT INTO gold_set_question (question_text, category, expected_urn_substring) VALUES ($1, $2, $3)",
            question_text,
            category,
            expected,
        )


async def run_gold_set(
    pool: asyncpg.Pool,
    *,
    authorization: str,
    knowledge_url: str,
    qdrant: AsyncQdrantClient,
    embedder: EmbeddingClient,
    glossary_terms: list[dict],
    llm: LLMClient,
) -> dict:
    questions = await pool.fetch(
        "SELECT question_text, category, expected_urn_substring FROM gold_set_question ORDER BY id"
    )
    results: list[dict] = []
    latencies: list[float] = []
    total_tokens = 0
    correct = 0
    grounded_count = 0

    for row in questions:
        start = time.monotonic()
        try:
            response = await context_builder_ask(
                query_text=row["question_text"],
                authorization=authorization,
                knowledge_url=knowledge_url,
                qdrant=qdrant,
                embedder=embedder,
                glossary_terms=glossary_terms,
                llm=llm,
            )
        except Exception as exc:  # noqa: BLE001 — one bad question must not abort the whole run
            logger.exception("gold set question failed: %s", row["question_text"])
            results.append({"question": row["question_text"], "error": str(exc)})
            continue
        elapsed = time.monotonic() - start
        latencies.append(elapsed)
        total_tokens += response["tokens"]["input"] + response["tokens"]["output"]
        is_correct = row["expected_urn_substring"] is None or any(
            row["expected_urn_substring"] in c for c in response["citations"]
        )
        correct += int(is_correct)
        grounded_count += int(response["grounded"])
        results.append(
            {
                "question": row["question_text"],
                "category": row["category"],
                "answer": response["answer"],
                "citations": response["citations"],
                "grounded": response["grounded"],
                "correct": is_correct,
                "latency_seconds": round(elapsed, 3),
            }
        )

    n = len(questions)
    latencies.sort()
    p95_index = max(0, int(len(latencies) * 0.95) - 1) if latencies else 0
    metrics = {
        "gold_set_size": n,
        "exactitude": (correct / n) if n else None,
        "groundedness_rate": (grounded_count / n) if n else None,
        "latency_p95_seconds": latencies[p95_index] if latencies else None,
        "total_tokens": total_tokens,
        # These need expert judgment / real usage patterns to compute —
        # marked as not measurable until a gold set is provided.
        "abstention_appropriee": "not measurable without a real gold set",
        "faux_refus": "not measurable without a real gold set",
    }
    await pool.execute("INSERT INTO eval_run (finished_at, metrics) VALUES (now(), $1::jsonb)", json.dumps(metrics))

    return {
        "metrics": metrics,
        "results": results,
    }


async def run_security_suite(*, knowledge_url: str, agent_token: str, editor_token: str) -> dict:
    """Zero tolerance security suite: an agent must never exceed its own or its
    mandant's rights. Extends the same scenario tested at the PDP layer —
    run here as a callable, repeatable API check.
    """
    checks = []
    async with httpx.AsyncClient(timeout=15.0) as http:
        # 1. The agent's own read access (viewer, should succeed).
        response = await http.get(f"{knowledge_url}/objects/Customer", headers={"Authorization": f"Bearer {agent_token}"})
        checks.append({"check": "agent_can_read_within_its_grant", "passed": response.status_code == 200})

        # 2. The agent attempting a write it doesn't have (viewer < editor
        # required) — MUST be denied. A single failure here is a security regression.
        response = await http.post(
            f"{knowledge_url}/objects/Customer/1/actions/putOnCreditHold",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"reason": "security suite probe"},
        )
        checks.append({"check": "agent_cannot_exceed_its_own_grant", "passed": response.status_code == 403})

        # 3. Sanity: the same write succeeds for a principal that actually
        # holds editor rights — proves check 2 failed for the right reason
        # (missing permission), not because the endpoint itself is broken.
        response = await http.post(
            f"{knowledge_url}/objects/Customer/1/actions/putOnCreditHold",
            headers={"Authorization": f"Bearer {editor_token}"},
            json={"reason": "security suite sanity check"},
        )
        checks.append({"check": "editor_can_use_the_same_endpoint", "passed": response.status_code == 200})

    all_passed = all(c["passed"] for c in checks)
    return {"checks": checks, "zero_tolerance_violations": sum(1 for c in checks if not c["passed"]), "passed": all_passed}
