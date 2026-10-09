"""Safe SQL / DuckDB identifier quoting.

Identifiers cannot be bound as parameters. Callers must validate against
this alphabet, then quote — never interpolate raw admin input.
"""

from __future__ import annotations

import re

# No quotes, whitespace, or punctuation that could break out of a quoted identifier.
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*){0,2}$")

_VALID_QUOTE_DIALECTS = frozenset({"postgres", "mysql", "mssql", "snowflake"})


def require_identifier(name: str, *, what: str = "identifier") -> str:
    if not name or not IDENTIFIER_RE.fullmatch(name):
        raise ValueError(
            f"invalid {what} {name!r} — must be a plain identifier with up to "
            "three dot-separated parts (e.g. 'orders', 'public.orders' or "
            "'analytics.public.orders')"
        )
    return name


def quote_identifier(name: str, *, dialect: str = "postgres") -> str:
    """Render each dot-separated part for the dialect. Safe only after `require_identifier`.

    Dialect quoting: postgres → "x", mysql → `x`, mssql → [x].
    Only those wire protocols are accepted; product aliases such as
    `singlestore` must be mapped before calling.
    Snowflake leaves identifiers unquoted so the server folds them to
    UPPERCASE (quoted names would be case-sensitive and miss default objects).
    """
    require_identifier(name)
    d = (dialect or "postgres").lower()
    if d not in _VALID_QUOTE_DIALECTS:
        raise ValueError(f"unsupported quote dialect {dialect!r} — must be one of {sorted(_VALID_QUOTE_DIALECTS)}")
    if d == "snowflake":
        return name
    if d == "mysql":
        return ".".join(f"`{part}`" for part in name.split("."))
    if d == "mssql":
        return ".".join(f"[{part}]" for part in name.split("."))
    return ".".join(f'"{part}"' for part in name.split("."))
