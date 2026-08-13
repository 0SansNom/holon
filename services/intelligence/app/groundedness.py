"""Groundedness checks for RAG answers — stdlib only (host-testable)."""

from __future__ import annotations

import re
from typing import Protocol


class _HasUrn(Protocol):
    urn: str


def check_groundedness(response_text: str, items: list[_HasUrn]) -> bool:
    """Require citations when context was provided; never invent URNs.

    V1 heuristic (not a second LLM pass): every cited URN must appear in
    the assembled context. Empty context → grounded only if the answer
    cites nothing. Non-empty context → at least one citation required.
    """
    cited = {c.strip() for c in re.findall(r"URN:\s*([^\]]+)\]", response_text)}
    known = {item.urn for item in items}
    if cited - known:
        return False
    if not items:
        return len(cited) == 0
    return len(cited) > 0 and cited.issubset(known)
