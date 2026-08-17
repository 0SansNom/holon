"""Knowledge public URL helpers — `/api/ontologies|holon/…` only.

Knowledge rejects bare internal paths (`/ontology`, `/actions`, …) via
``PublicApiOnlyMiddleware``. Cross-service callers must use the same
surface the SPA and Experience proxy already use.
"""

from __future__ import annotations

import os

_WORKSPACE_ID = os.environ.get("HOLON_WORKSPACE_ID", "main")


def ontology_url(knowledge_url: str, path: str = "") -> str:
    suffix = path if path.startswith("/") else f"/{path}" if path else ""
    return f"{knowledge_url.rstrip('/')}/api/ontologies/{_WORKSPACE_ID}{suffix}"


def holon_url(knowledge_url: str, path: str = "") -> str:
    suffix = path if path.startswith("/") else f"/{path}" if path else ""
    return f"{knowledge_url.rstrip('/')}/api/holon{suffix}"
