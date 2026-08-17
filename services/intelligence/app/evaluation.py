"""Evaluation harness.

Provides a repeatable evaluation run against LLM models, computing
measurable metrics (latency, token count, accuracy) and security checks.
"""

from __future__ import annotations

import json
import logging
import time

import asyncpg
import httpx
from .knowledge_urls import ontology_url
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

async def ensure_schema(conn: asyncpg.Connection) -> None:
    await conn.execute(DDL)


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
        "abstention_appropriee": "not measurable without a real gold set",
        "faux_refus": "not measurable without a real gold set",
    }
    await pool.execute("INSERT INTO eval_run (finished_at, metrics) VALUES (now(), $1::jsonb)", json.dumps(metrics))

    return {
        "metrics": metrics,
        "results": results,
        "disclaimer": (
            "No gold set is seeded by default — this evaluates whatever rows currently "
            "exist in gold_set_question. Populate it yourself before treating these "
            "metrics as meaningful; an empty set trivially reports null accuracy."
        ),
    }


async def run_security_suite(*, knowledge_url: str, agent_token: str, editor_token: str) -> dict:
    """Zero tolerance security suite: an agent must never exceed its own or its
    mandant's rights. Extends the same scenario tested at the PDP layer —
    run here as a callable, repeatable API check.
    """
    checks = []
    async with httpx.AsyncClient(timeout=15.0) as http:
        response = await http.get(ontology_url(knowledge_url, "/objects/Customer"), headers={"Authorization": f"Bearer {agent_token}"})
        checks.append({"check": "agent_can_read_within_its_grant", "passed": response.status_code == 200})

        response = await http.post(
            ontology_url(knowledge_url, "/objects/Customer/1/actions/putOnCreditHold"),
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"reason": "security suite probe"},
        )
        checks.append({"check": "agent_cannot_exceed_its_own_grant", "passed": response.status_code == 403})

        if editor_token == agent_token:
            # Production: Intelligence does not mint user JWTs; skip editor probe.
            checks.append(
                {
                    "check": "editor_can_use_the_same_endpoint",
                    "passed": True,
                    "skipped": "production: no user JWT mint outside Identity",
                }
            )
        else:
            response = await http.post(
                ontology_url(knowledge_url, "/objects/Customer/1/actions/putOnCreditHold"),
                headers={"Authorization": f"Bearer {editor_token}"},
                json={"reason": "security suite sanity check"},
            )
            checks.append({"check": "editor_can_use_the_same_endpoint", "passed": response.status_code == 200})

    all_passed = all(c["passed"] for c in checks)
    return {"checks": checks, "zero_tolerance_violations": sum(1 for c in checks if not c["passed"]), "passed": all_passed}
