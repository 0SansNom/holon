"""Vector Store — Qdrant wrapper for ontology metadata and glossary indexing."""

from __future__ import annotations

import logging
import os
import uuid

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from .embeddings import EmbeddingClient
from .knowledge_urls import holon_url, ontology_url

logger = logging.getLogger("intelligence.vector_store")

COLLECTION_NAME = "holon_semantic_index"


def metadata_point_id(*, tenant_id: str, source: str, urn: str) -> str:
    """Deterministic Qdrant point id scoped by tenant (avoids cross-tenant collisions)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{tenant_id}:{source}:{urn}"))


async def ensure_collection(client: AsyncQdrantClient, dimension: int) -> None:
    collections = await client.get_collections()
    if COLLECTION_NAME not in [c.name for c in collections.collections]:
        await client.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
        )


async def purge_untagged_points(client: AsyncQdrantClient) -> int:
    """Delete legacy points that lack tenant_id (pre-P2 index). Returns count deleted."""
    deleted = 0
    next_offset = None
    while True:
        records, next_offset = await client.scroll(
            collection_name=COLLECTION_NAME,
            limit=64,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )
        if not records:
            break
        orphan_ids = [
            point.id
            for point in records
            if not (point.payload or {}).get("tenant_id")
        ]
        if orphan_ids:
            await client.delete(collection_name=COLLECTION_NAME, points_selector=orphan_ids)
            deleted += len(orphan_ids)
        if next_offset is None:
            break
    if deleted:
        logger.info("purged %d Qdrant points missing tenant_id", deleted)
    return deleted


async def maybe_rebuild_collection(client: AsyncQdrantClient, dimension: int) -> bool:
    """If HOLON_QDRANT_REBUILD=1, drop and recreate the collection (local DX)."""
    raw = (os.environ.get("HOLON_QDRANT_REBUILD") or "").strip().lower()
    if raw not in {"1", "true", "yes"}:
        return False
    collections = await client.get_collections()
    names = {c.name for c in collections.collections}
    if COLLECTION_NAME in names:
        await client.delete_collection(COLLECTION_NAME)
        logger.warning("HOLON_QDRANT_REBUILD: deleted collection %s", COLLECTION_NAME)
    await client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=dimension, distance=Distance.COSINE),
    )
    return True


async def index_metadata(
    client: AsyncQdrantClient,
    embedder: EmbeddingClient,
    *,
    knowledge_url: str,
    token: str,
    tenant_id: str,
    workspace_id: str,
) -> int:
    """Safe to re-run: each point's id is a deterministic hash of tenant+source+urn,
    so re-indexing updates in place rather than duplicating.
    """
    headers = {"Authorization": f"Bearer {token}"}
    documents: list[dict] = []

    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.get(ontology_url(knowledge_url, "/objectTypes"), headers=headers)
        response.raise_for_status()
        for data in response.json():
            documents.append(
                {
                    "text": f"{data['name']}: {data['description']}",
                    "source": "object_type",
                    "urn": data["urn"],
                    "object_type": data["name"],
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                }
            )

        response = await http.get(holon_url(knowledge_url, "/actions"), headers=headers)
        response.raise_for_status()
        for action in response.json():
            documents.append(
                {
                    "text": f"{action['name']}: {action['description']}",
                    "source": "action",
                    "urn": action["name"],
                    "object_type": action["target_object_type"],
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                }
            )

        response = await http.get(holon_url(knowledge_url, "/glossary"), headers=headers)
        response.raise_for_status()
        for term in response.json():
            synonyms = ", ".join(term["synonyms"])
            documents.append(
                {
                    "text": f"{term['term']} ({synonyms}): {term['definition']}",
                    "source": "glossary",
                    "urn": term["term"],
                    "object_type": None,
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                }
            )

    if not documents:
        return 0

    vectors = await embedder.embed([doc["text"] for doc in documents])
    points = [
        PointStruct(
            id=metadata_point_id(tenant_id=tenant_id, source=doc["source"], urn=doc["urn"]),
            vector=vector,
            payload=doc,
        )
        for doc, vector in zip(documents, vectors)
    ]
    await client.upsert(collection_name=COLLECTION_NAME, points=points)
    return len(points)


async def semantic_search(
    client: AsyncQdrantClient,
    embedder: EmbeddingClient,
    *,
    query_text: str,
    tenant_id: str,
    limit: int = 5,
) -> list[dict]:
    [query_vector] = await embedder.embed([query_text])
    results = await client.query_points(
        collection_name=COLLECTION_NAME,
        query=query_vector,
        query_filter=Filter(
            must=[FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))]
        ),
        limit=limit,
    )
    return [{"score": point.score, **point.payload} for point in results.points]
