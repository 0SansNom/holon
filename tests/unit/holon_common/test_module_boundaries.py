"""Tests for Module Boundaries."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICES_DIR = REPO_ROOT / "services"
HOLON_COMMON_DIR = REPO_ROOT / "libs" / "holon_common"

SERVICE_NAMES = sorted(p.name for p in SERVICES_DIR.iterdir() if p.is_dir())


def _absolute_import_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_no_service_imports_another_services_code() -> None:
    violations = []
    for service in SERVICE_NAMES:
        other_services = set(SERVICE_NAMES) - {service}
        app_dir = SERVICES_DIR / service / "app"
        for path in sorted(app_dir.rglob("*.py")):
            bad = _absolute_import_names(path) & other_services
            if bad:
                violations.append(f"{path.relative_to(REPO_ROOT)} imports {sorted(bad)}")
    assert not violations, (
        "Module boundary violation(s) — a service must only import its own app package "
        "or holon_common, never another service directly:\n" + "\n".join(violations)
    )


def test_holon_common_does_not_import_back_into_a_service() -> None:
    """A dependency arrow must never point from shared code back down into one platform's own."""
    violations = []
    for path in sorted(HOLON_COMMON_DIR.rglob("*.py")):
        bad = _absolute_import_names(path) & set(SERVICE_NAMES)
        if bad:
            violations.append(f"{path.relative_to(REPO_ROOT)} imports {sorted(bad)}")
    assert not violations, "Module boundary violation(s) — holon_common must not import a service:\n" + "\n".join(violations)
