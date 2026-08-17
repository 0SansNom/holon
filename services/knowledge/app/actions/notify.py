"""Best-effort HTTP notification delivery for Action Types (P2c).

Posts a JSON payload to the Action Type's ``notify_webhook`` URL (or the
env fallback ``HOLON_ACTION_NOTIFY_WEBHOOK``). Never raises into the
Action apply path — delivery failures are logged only, same posture as
``function_side_effect``.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger("knowledge.actions.notify")


async def deliver_action_notification(
    *,
    webhook_url: Optional[str],
    event: str,
    payload: dict[str, Any],
    timeout_seconds: float = 5.0,
) -> None:
    url = (webhook_url or "").strip() or (os.environ.get("HOLON_ACTION_NOTIFY_WEBHOOK") or "").strip()
    if not url:
        return
    body = {"event": event, **payload}
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
    except Exception:
        logger.exception("action notification webhook failed for event=%s url=%s", event, url)
