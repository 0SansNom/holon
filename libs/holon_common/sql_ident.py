"""Safe SQL / DuckDB identifier quoting.

Identifiers cannot be bound as parameters. Callers must validate against
this alphabet, then quote — never interpolate raw admin input.
"""

from __future__ import annotations

import re

# Optionally schema-qualified (`public.orders`). No quotes, whitespace, or
# punctuation that could break out of a quoted identifier.
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def require_identifier(name: str, *, what: str = "identifier") -> str:
    if not name or not IDENTIFIER_RE.match(name):
        raise ValueError(
            f"invalid {what} {name!r} — must be a plain identifier, optionally "
            "schema-qualified (e.g. 'orders' or 'public.orders')"
        )
    return name


def quote_identifier(name: str) -> str:
    """Quote each dot-separated part. Safe only after `require_identifier`."""
    require_identifier(name)
    return ".".join(f'"{part}"' for part in name.split("."))
