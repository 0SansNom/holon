"""MongoDB connector — document store source.

Kept in its own module rather than folded into `connector.py`: the module
boundary reflects the source system. Same rule as everywhere else in
Connectivity — read-only, never writes back to the source.

`pymongo` is a synchronous driver, so the read runs via `asyncio.to_thread`
at the call site in `main.py` — the same pattern already used for
pyiceberg's synchronous write, not a new async idiom.
"""

from __future__ import annotations

from pymongo import MongoClient

DATABASE = "support_desk"
COLLECTION = "support_tickets"


def read_support_tickets(mongo_url: str) -> list[dict]:
    client = MongoClient(mongo_url)
    try:
        cursor = client[DATABASE][COLLECTION].find({}, {"_id": 0}).sort("id", 1)
        return list(cursor)
    finally:
        client.close()
