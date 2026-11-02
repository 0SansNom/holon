"""Identity unit tests import ``app`` from services/identity.

``deps`` reads process env at import. Defaults here match the other identity
unit modules so a later import does not depend on the developer shell.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _entry in (_REPO / "libs", _REPO / "services" / "identity"):
    _path = str(_entry)
    if _path not in sys.path:
        sys.path.insert(0, _path)

os.environ.setdefault("HOLON_TENANT_ID", "acme")
os.environ.setdefault("HOLON_WORKSPACE_ID", "main")
os.environ.setdefault("HOLON_JWT_SECRET", "unit-test-jwt-secret-must-be-long")
os.environ.setdefault("HOLON_DB_URL", "postgresql://localhost/identity")
os.environ.setdefault("HOLON_SPICEDB_URL", "http://localhost:8443")
os.environ.setdefault("HOLON_SPICEDB_PRESHARED_KEY", "test")
os.environ.setdefault("HOLON_SPICEDB_SCHEMA_PATH", "schema.zed")
os.environ.setdefault("HOLON_OPA_URL", "http://localhost:8181")
os.environ.setdefault("HOLON_KAFKA_BOOTSTRAP", "localhost:9092")
os.environ.setdefault("HOLON_ALLOW_LOCAL_USER_MINT", "1")
