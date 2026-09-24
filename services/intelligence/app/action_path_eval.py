"""Action-path evaluation — semantic / criteria outcomes without RAG/LLM deps.

Separated from ``evaluation.py`` so unit tests can import this module without
pulling voyageai / qdrant / sentence-transformers.
"""

from __future__ import annotations

from typing import Any, Optional

import httpx

from .knowledge_urls import ontology_url


async def run_action_path_suite(*, knowledge_url: str, editor_token: str) -> dict:
    """Measure syntax / semantic / authz path outcomes on ontology Actions.

    Complements the gold set (RAG quality) and security suite (grant
    boundaries): this suite checks that invalid *world states* are refused
    by submission_criteria (400 ActionValidationFailed), not merely that
    the LLM was told not to do them.
    """
    checks: list[dict] = []
    headers = {"Authorization": f"Bearer {editor_token}"}

    async with httpx.AsyncClient(timeout=15.0) as http:
        # Semantic refuse: delivered order cannot be cancelled (criteria).
        response = await http.post(
            ontology_url(knowledge_url, "/objects/Order/1/actions/cancelPending"),
            headers=headers,
            json={"reason": "path suite — expect criteria reject"},
        )
        body = _safe_json(response)
        checks.append(
            {
                "check": "semantic_reject_non_pending_order",
                "passed": response.status_code == 400
                and (body or {}).get("errorName") == "ActionValidationFailed",
                "status_code": response.status_code,
            }
        )

        # Happy semantic path: a pending order that is not already cancelled.
        pending_id = await _find_cancellable_order_id(http, knowledge_url, headers)
        if pending_id is None:
            checks.append(
                {
                    "check": "semantic_apply_pending_order",
                    "passed": False,
                    "skipped": "no cancellable pending order available",
                }
            )
        else:
            response = await http.post(
                ontology_url(knowledge_url, f"/objects/Order/{pending_id}/actions/cancelPending"),
                headers=headers,
                json={"reason": "path suite — expect apply"},
            )
            body = _safe_json(response)
            checks.append(
                {
                    "check": "semantic_apply_pending_order",
                    "passed": response.status_code == 200 and (body or {}).get("status") == "applied",
                    "status_code": response.status_code,
                    "order_id": pending_id,
                }
            )

        # Authz refuse is covered by run_security_suite; mirror one read here
        # so path metrics stay self-contained when security is skipped.
        response = await http.get(
            ontology_url(knowledge_url, "/objects/Order"),
            headers=headers,
        )
        checks.append(
            {
                "check": "editor_can_list_orders",
                "passed": response.status_code == 200,
                "status_code": response.status_code,
            }
        )

    passed = sum(1 for c in checks if c.get("passed"))
    failed = sum(1 for c in checks if not c.get("passed") and not c.get("skipped"))
    return {
        "checks": checks,
        "passed_count": passed,
        "failed_count": failed,
        "passed": failed == 0,
        "path_failure_rate": (failed / len(checks)) if checks else None,
    }


def _safe_json(response: httpx.Response) -> Optional[dict]:
    try:
        data: Any = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


async def _find_cancellable_order_id(
    http: httpx.AsyncClient, knowledge_url: str, headers: dict
) -> Optional[int]:
    response = await http.get(ontology_url(knowledge_url, "/objects/Order"), headers=headers)
    if response.status_code != 200:
        return None
    rows = response.json()
    if not isinstance(rows, list):
        return None
    for row in rows:
        if row.get("status") == "pending" and row.get("cancelled") is not True:
            try:
                return int(row["id"])
            except (KeyError, TypeError, ValueError):
                continue
    return None
