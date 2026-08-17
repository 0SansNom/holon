"""Unit-test path bootstrap (no compose stack).

Only ``libs/`` is added globally. Service packages all use the top-level
name ``app`` — putting more than one service root on ``sys.path`` makes
imports resolve to the wrong service. Each unit module that needs a
service package must insert that service root itself (and clear
``sys.modules['app*']`` when required).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_LIBS = str(REPO_ROOT / "libs")
if _LIBS not in sys.path:
    sys.path.insert(0, _LIBS)
