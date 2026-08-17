#!/usr/bin/env python3
"""Merge deploy/observability recording + alert rule files into the Helm chart copy.

No PyYAML dependency — concatenates `groups:` lists by text.

Usage:
  python3 scripts/sync_observability_rules.py
  python3 scripts/sync_observability_rules.py --check
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC_RECORDING = REPO / "deploy" / "observability" / "recording-rules.yaml"
SRC_ALERTS = REPO / "deploy" / "observability" / "alerts.yaml"
DST = REPO / "deploy" / "helm" / "holon" / "files" / "observability" / "rules.yaml"

_GROUPS_HEADER = re.compile(r"^groups:\s*\n", re.MULTILINE)


def _groups_body(path: Path) -> str:
    text = path.read_text()
    if not _GROUPS_HEADER.match(text):
        raise SystemExit(f"{path}: expected leading 'groups:'")
    body = _GROUPS_HEADER.sub("", text, count=1)
    return body.rstrip() + "\n"


def render() -> str:
    return "groups:\n" + _groups_body(SRC_RECORDING) + _groups_body(SRC_ALERTS)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if chart file is stale")
    args = parser.parse_args()
    body = render()
    if args.check:
        current = DST.read_text() if DST.exists() else ""
        if current != body:
            print(f"stale: {DST} — run scripts/sync_observability_rules.py", file=sys.stderr)
            return 1
        print(f"ok: {DST}")
        return 0
    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(body)
    print(f"wrote {DST}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
