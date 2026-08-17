#!/usr/bin/env python3
"""Migrate raise HTTPException(...) → HolonError with stable error names.

Idempotent. Rewrites service modules in-place.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# detail substring (lower) → (factory method or None, error_name)
# factory None → HolonError.from_http / HolonError(status, name, detail)
NAME_RULES: list[tuple[str, str]] = [
    ("unknown objecttype", "ObjectTypeNotFound"),
    ("unknown object type", "ObjectTypeNotFound"),
    ("unknown value type", "ValueTypeNotFound"),
    ("unknown action type", "ActionTypeNotFound"),
    ("unknown action:", "ActionNotFound"),
    ("unknown relationtype", "RelationTypeNotFound"),
    ("unknown relation type", "RelationTypeNotFound"),
    ("unknown interface", "InterfaceNotFound"),
    ("unknown marking", "MarkingNotFound"),
    ("unknown shared property type", "SharedPropertyTypeNotFound"),
    ("unknown branch", "BranchNotFound"),
    ("unknown glossary term", "GlossaryTermNotFound"),
    ("unknown object set", "ObjectSetNotFound"),
    ("unknown principal", "PrincipalNotFound"),
    ("unknown tenant", "TenantNotFound"),
    ("unknown workspace", "WorkspaceNotFound"),
    ("unknown project", "ProjectNotFound"),
    ("dataset ", "DatasetNotFound"),
    ("has never been synced", "DatasetNotFound"),
    ("not catalogued", "ObjectTypeNotCatalogued"),
    ("already exists", "AlreadyExists"),
    ("rebac_denied", "PermissionDenied"),
    ("permission", "PermissionDenied"),
    ("tenant mismatch", "TenantMismatch"),
    ("disabled", "PrincipalDisabled"),
    ("invalid principal", "InvalidCredentials"),
    ("invalid token", "InvalidToken"),
    ("authentication required", "AuthenticationRequired"),
    ("oidc is not configured", "OidcNotConfigured"),
    ("oidc", "OidcError"),
    ("instance_ids must be non-empty", "EmptyBatch"),
    ("instance_ids capped", "BatchTooLarge"),
    ("property_mapping must", "InvalidPropertyMapping"),
    ("casts must", "InvalidCasts"),
    ("join requires", "InvalidJoin"),
    ("unsupported relationtype storage", "UnsupportedStorageKind"),
    ("no execution_run", "ExecutionRunNotFound"),
    ("no agent session", "AgentSessionNotFound"),
    ("no agent_session", "AgentSessionNotFound"),
    ("tool plugin", "ToolPluginError"),
    ("metrics unauthorized", "MetricsUnauthorized"),
    ("cannot mint", "ForbiddenMint"),
    ("declares no ", "ApplicationSurfaceMissing"),
    ("unexpected object-set", "UpstreamBadResponse"),
    ("tagging isn't supported", "UnsupportedResourceType"),
    ("a collection named", "CollectionAlreadyExists"),
    ("backing objecttype", "ObjectTypeNotFound"),
    ("not found", "NotFound"),
]

STATUS_FACTORY = {
    400: "invalid_argument",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    429: "rate_limited",
    503: "unavailable",
    500: "internal",
}


def infer_name(detail_expr: str, status: str) -> str:
    blob = detail_expr.lower()
    for needle, name in NAME_RULES:
        if needle in blob:
            if name == "AlreadyExists":
                # Prefer more specific when possible
                if "value type" in blob:
                    return "ValueTypeAlreadyExists"
                if "shared property" in blob:
                    return "SharedPropertyTypeAlreadyExists"
                if "action type" in blob:
                    return "ActionTypeAlreadyExists"
                if "interface" in blob:
                    return "InterfaceAlreadyExists"
                if "marking" in blob:
                    return "MarkingAlreadyExists"
                if "relationtype" in blob or "relation type" in blob:
                    return "RelationTypeAlreadyExists"
                if "glossary" in blob:
                    return "GlossaryTermAlreadyExists"
                if "tenant" in blob:
                    return "TenantAlreadyExists"
                if "workspace" in blob:
                    return "WorkspaceAlreadyExists"
                if "collection" in blob:
                    return "CollectionAlreadyExists"
            if name == "PrincipalDisabled" and "tenant" in blob:
                return "TenantDisabled"
            if name == "PrincipalDisabled" and "workspace" in blob:
                return "WorkspaceDisabled"
            if name == "PermissionDenied" and "marking" in blob:
                return "MarkingDenied"
            return name
    try:
        code = int(status)
    except ValueError:
        return "RequestFailed"
    return {
        400: "InvalidRequest",
        401: "Unauthorized",
        403: "Forbidden",
        404: "NotFound",
        409: "Conflict",
        422: "ValidationFailed",
        429: "RateLimited",
        502: "BadGateway",
        503: "Unavailable",
        500: "InternalError",
    }.get(code, "RequestFailed")


def ensure_import(text: str) -> str:
    if re.search(r"\bHolonError\b", text) and "from holon_common import" in text:
        # Ensure HolonError is in an existing holon_common import
        if re.search(r"from holon_common import[^\n]*HolonError", text):
            return text
        if re.search(r"from holon_common import \(\n", text):
            return re.sub(
                r"(from holon_common import \(\n)",
                r"\1    HolonError,\n",
                text,
                count=1,
            )
        return re.sub(
            r"from holon_common import ([^\n]+)",
            r"from holon_common import HolonError, \1",
            text,
            count=1,
        )
    if "from holon_common import" in text:
        if re.search(r"from holon_common import \(\n", text):
            return re.sub(
                r"(from holon_common import \(\n)",
                r"\1    HolonError,\n",
                text,
                count=1,
            )
        return re.sub(
            r"from holon_common import ([^\n]+)",
            r"from holon_common import HolonError, \1",
            text,
            count=1,
        )
    # Insert after fastapi import
    m = re.search(r"from fastapi import ([^\n]+)\n", text)
    if m:
        return text[: m.end()] + "from holon_common import HolonError\n" + text[m.end() :]
    return "from holon_common import HolonError\n" + text


def rewrite_raise(status: str, detail: str, from_exc: str) -> str:
    status = status.strip()
    detail = detail.strip()
    name = infer_name(detail, status if status.isdigit() else "400")
    suffix = f" from {from_exc}" if from_exc else ""

    # Dynamic status variable
    if not status.isdigit():
        return f'raise HolonError.from_http({status}, {detail}, error_name={name!r}){suffix}'

    code = int(status)
    factory = STATUS_FACTORY.get(code)
    if factory and code != 502:
        return f'raise HolonError.{factory}({name!r}, {detail}){suffix}'
    return f"raise HolonError.from_http({code}, {detail}, error_name={name!r}){suffix}"


# Matches single-line and simple multi-line HTTPException raises.
RAISE_RE = re.compile(
    r"raise\s+HTTPException\(\s*"
    r"status_code\s*=\s*(?P<status>[^,]+?)\s*,\s*"
    r"detail\s*=\s*(?P<detail>(?:[^()]|\([^)]*\))+?)\s*"
    r"\)(?P<from>\s+from\s+\w+)?",
    re.MULTILINE,
)


def migrate_file(path: Path) -> bool:
    original = path.read_text()
    if "raise HTTPException" not in original:
        return False

    def repl(match: re.Match[str]) -> str:
        from_part = match.group("from") or ""
        from_exc = from_part.replace("from", "").strip() if from_part else ""
        return rewrite_raise(match.group("status"), match.group("detail"), from_exc)

    text = RAISE_RE.sub(repl, original)
    if text == original:
        print(f"WARN no rewrite matched: {path}", file=sys.stderr)
        return False

    text = ensure_import(text)
    # Drop unused HTTPException from fastapi imports when no longer referenced
    if "HTTPException" not in text.replace("HTTPException", "", text.count("from fastapi")):
        pass
    if "raise HTTPException" not in text and "HTTPException" in text:
        # Remove HTTPException from fastapi import lists
        def drop_http(m: re.Match[str]) -> str:
            parts = [p.strip() for p in m.group(1).split(",")]
            parts = [p for p in parts if p and p != "HTTPException"]
            return "from fastapi import " + ", ".join(parts)

        text = re.sub(r"from fastapi import ([^\n]+)", drop_http, text, count=1)
        # Clean double commas / trailing
        text = re.sub(r"from fastapi import ,\s*", "from fastapi import ", text)
        text = re.sub(r"from fastapi import\s*\n", "from fastapi import Request\n", text)  # shouldn't happen

    path.write_text(text)
    remaining = text.count("raise HTTPException")
    print(f"ok {path} remaining_raises={remaining}")
    return True


def main() -> int:
    roots = [
        REPO / "services",
    ]
    files: list[Path] = []
    for root in roots:
        files.extend(root.rglob("*.py"))
    changed = 0
    for path in sorted(files):
        if path.name.startswith("test_"):
            continue
        if "raise HTTPException" in path.read_text():
            if migrate_file(path):
                changed += 1
    print(f"migrated {changed} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
