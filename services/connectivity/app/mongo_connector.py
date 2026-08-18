"""MongoDB connector — document store source ingestion."""

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
