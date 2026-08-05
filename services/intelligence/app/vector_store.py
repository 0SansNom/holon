"""Vector Store — thin Qdrant wrapper (metadata
only, never bulk instance vectorization).

Indexed here: ObjectType/Action definitions (their descriptions) and
glossary terms (with synonyms) — pulled from Knowledge's own **HTTP**
API, never its database directly, keeping the same platform boundary
every other cross-service interaction in this build already respects.
All three source endpoints (`/ontology/{name}`, `/actions`, `/glossary`)
are auth-only in Knowledge, so a bare service-account JWT is enough.

Deliberately *not* indexing instance-level free text (e.g. ProductReview
comments) in this round: doing so correctly would need the indexer to
carry real ReBAC/ABAC rights on that ObjectType, which raises exactly the
kind of permission-plumbing this metadata-only scope was chosen to avoid.
A real per-tenant instance-text index is a further step, not silently
assumed to already work.
"""

from __future__ import annotations

import logging
import uuid

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from .embeddings import EmbeddingClient

logger = logging.getLogger("intelligence.vector_store")

COLLECTION_NAME = "holon_semantic_index"

_OBJECT_TYPES = ["Customer", "Order", "SupportTicket", "ProductReview", "Supplier", "InventoryLevel"]


async def ensure_collection(client: AsyncQdrantClient, dimension: int) -> None:
    collections = await client.get_collections()
    if COLLECTION_NAME not in [c.name for c in collections.collections]:
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )


async def index_metadata(
    client: AsyncQdrantClient, embedder: EmbeddingClient, *, knowledge_url: str, token: str
) -> int:
    """Safe to re-run: each point's id is a deterministic hash of its
    source, so re-indexing updates in place rather than duplicating.
    """
    headers = {"Authorization": f"Bearer {token}"}
    documents: list[dict] = []

    async with httpx.AsyncClient(timeout=30.0) as http:
        for object_type in _OBJECT_TYPES:
            response = await http.get(f"{knowledge_url}/ontology/{object_type}", headers=headers)
            response.raise_for_status()
            data = response.json()
            documents.append(
                {
                    "text": f"{object_type}: {data['description']}",
                    "source": "object_type",
                    "urn": data["urn"],
                    "object_type": object_type,
                }
            )

        response = await http.get(f"{knowledge_url}/actions", headers=headers)
        response.raise_for_status()
        for action in response.json():
            documents.append(
                {
                    "text": f"{action['name']}: {action['description']}",
                    "source": "action",
                    "urn": action["name"],
                    "object_type": action["target_object_type"],
                }
            )

        response = await http.get(f"{knowledge_url}/glossary", headers=headers)
        response.raise_for_status()
        for term in response.json():
            synonyms = ", ".join(term["synonyms"])
            documents.append(
                {
                    "text": f"{term['term']} ({synonyms}): {term['definition']}",
                    "source": "glossary",
                    "urn": term["term"],
                    "object_type": None,
                }
            )

    if not documents:
        return 0

    vectors = await embedder.embed([doc["text"] for doc in documents])
    points = [
        PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc['source']}:{doc['urn']}")),
            vector=vector,
            payload=doc,
        )
        for doc, vector in zip(documents, vectors)
    ]
    await client.upsert(collection_name=COLLECTION_NAME, points=points)
    return len(points)


async def semantic_search(
    client: AsyncQdrantClient, embedder: EmbeddingClient, *, query_text: str, limit: int = 5
) -> list[dict]:
    [query_vector] = await embedder.embed([query_text])
    results = await client.query_points(collection_name=COLLECTION_NAME, query=query_vector, limit=limit)
    return [{"score": point.score, **point.payload} for point in results.points]
