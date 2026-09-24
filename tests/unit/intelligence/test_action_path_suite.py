"""Unit tests for action-path evaluation (no live stack / LLM)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "libs"))
sys.path.insert(0, str(REPO / "services" / "intelligence"))

from app import action_path_eval  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def test_action_path_suite_scores_semantic_reject_and_apply() -> None:
    async def _run() -> None:
        posts = [
            _FakeResponse(400, {"errorName": "ActionValidationFailed", "detail": "order is not cancellable"}),
            _FakeResponse(200, {"status": "applied", "cancelled": True}),
        ]
        gets = [
            _FakeResponse(
                200,
                [
                    {"id": 1, "status": "delivered"},
                    {"id": 11, "status": "pending", "cancelled": False},
                ],
            ),
            _FakeResponse(200, [{"id": 1, "status": "delivered"}]),
        ]

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=posts)
        mock_client.get = AsyncMock(side_effect=gets)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(action_path_eval.httpx, "AsyncClient", return_value=mock_client):
            result = await action_path_eval.run_action_path_suite(
                knowledge_url="http://knowledge:8003",
                editor_token="tok",
            )

        assert result["passed"] is True
        assert result["failed_count"] == 0
        assert result["path_failure_rate"] == 0
        by_name = {c["check"]: c for c in result["checks"]}
        assert by_name["semantic_reject_non_pending_order"]["passed"] is True
        assert by_name["semantic_apply_pending_order"]["passed"] is True
        assert by_name["semantic_apply_pending_order"]["order_id"] == 11

    asyncio.run(_run())


def test_action_path_suite_fails_when_criteria_are_not_enforced() -> None:
    async def _run() -> None:
        posts = [
            _FakeResponse(200, {"status": "applied"}),  # wrongly allowed on delivered
        ]
        gets = [
            _FakeResponse(200, []),
            _FakeResponse(200, []),
        ]
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=posts)
        mock_client.get = AsyncMock(side_effect=gets)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(action_path_eval.httpx, "AsyncClient", return_value=mock_client):
            result = await action_path_eval.run_action_path_suite(
                knowledge_url="http://knowledge:8003",
                editor_token="tok",
            )

        assert result["passed"] is False
        assert result["failed_count"] >= 1
        assert result["path_failure_rate"] is not None and result["path_failure_rate"] > 0

    asyncio.run(_run())
