"""Put the Knowledge service on ``sys.path`` for this directory only.

Service packages all share the top-level name ``app``. The root conftest
snapshots ``app.*`` per test file and restores it before each test, so
inserting this path at collection does not leak into another service.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _entry in (_REPO / "libs", _REPO / "services" / "knowledge"):
    _path = str(_entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)
