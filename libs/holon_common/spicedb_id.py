"""SpiceDB object-id encoding (no network deps).

SpiceDB object IDs disallow ':' and '.'. Encoding is injective: '_' →
'__', '.' → '_d', ':' → '_c', so 'jane.doe' and 'jane_doe' never share
a userset.
"""

from __future__ import annotations


def spicedb_object_id(urn: str) -> str:
    return urn.replace("_", "__").replace(".", "_d").replace(":", "_c")


def index_by_spicedb_object_id(rows, *, urn_key: str = "urn") -> dict:
    """Map each row's spicedb_object_id(urn) to the row."""
    return {spicedb_object_id(row[urn_key]): row for row in rows}
