"""holon_osdk — the OSDK generator: walks the live ontology
(`schema.py`) and renders typed Python (`emit_python.py`) or TypeScript
(`emit_typescript.py`) clients from it. See `cli/holon.py`'s
`codegen` subcommand for the entry point.
"""

from __future__ import annotations

from .emit_python import emit_python
from .emit_typescript import emit_typescript
from .schema import OntologySchema, fetch_schema

__all__ = ["fetch_schema", "emit_python", "emit_typescript", "OntologySchema"]
