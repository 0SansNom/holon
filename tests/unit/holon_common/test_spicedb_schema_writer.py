"""Identity is the only service that calls SpiceDB write_schema."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES = REPO_ROOT / "services"


def _write_schema_call_files() -> list[Path]:
    hits: list[Path] = []
    for path in SERVICES.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "write_schema":
                hits.append(path.relative_to(REPO_ROOT))
                break
    return hits


def test_only_identity_calls_write_schema() -> None:
    hits = _write_schema_call_files()
    assert hits, "expected Identity to call write_schema"
    unexpected = [p for p in hits if p.parts[1] != "identity"]
    assert not unexpected, (
        "write_schema must be Identity-only; also found:\n"
        + "\n".join(str(p) for p in unexpected)
    )
