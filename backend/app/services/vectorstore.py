"""Local Qdrant vector store — no API keys, no cloud.

Rules enforced here:
- Never delete/recreate an existing collection.
- If a collection exists with a different vector size than the configured
  model produces, raise instead of writing corrupt data (this guards the
  legacy 512-dim CLIP collection against the local 384-dim text model).
"""
from __future__ import annotations

from typing import Any, Iterable, Sequence

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from .. import config

_client: QdrantClient | None = None


def get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(url=config.QDRANT_URL, timeout=30)
    return _client


def ping() -> bool:
    try:
        get_client().get_collections()
        return True
    except Exception:
        return False


def collection_info(name: str | None = None) -> dict[str, Any]:
    coll = name or config.QDRANT_COLLECTION
    info = get_client().get_collection(coll)
    vectors = info.config.params.vectors
    size = getattr(vectors, "size", None)
    return {
        "name": coll,
        "dim": size,
        "distance": getattr(getattr(vectors, "distance", None), "value", None),
        "points": info.points_count,
        "status": str(info.status),
    }


def ensure_collection(dim: int, name: str | None = None) -> dict[str, Any]:
    coll = name or config.QDRANT_COLLECTION
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    if coll in existing:
        info = collection_info(coll)
        if info["dim"] != dim:
            raise RuntimeError(
                f"Collection '{coll}' exists with {info['dim']}-dim vectors but the "
                f"configured model produces {dim}-dim vectors. Refusing to write. "
                "Create a new collection name instead of recreating existing ones."
            )
        return info
    client.create_collection(
        collection_name=coll,
        vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
    )
    return collection_info(coll)


def upsert(
    ids: Sequence[str],
    vectors: Sequence[Sequence[float]],
    payloads: Sequence[dict[str, Any]],
    name: str | None = None,
) -> None:
    if not ids:
        return
    coll = name or config.QDRANT_COLLECTION
    get_client().upsert(
        collection_name=coll,
        points=[
            qm.PointStruct(
                id=pid,
                vector=[float(x) for x in vec],
                payload=dict(pl),
            )
            for pid, vec, pl in zip(ids, vectors, payloads)
        ],
        wait=True,
    )


def search(
    vector: Sequence[float],
    limit: int = 10,
    score_threshold: float | None = None,
    must: Sequence[qm.FieldCondition] | None = None,
    must_not: Sequence[qm.FieldCondition] | None = None,
    name: str | None = None,
) -> list[qm.ScoredPoint]:
    coll = name or config.QDRANT_COLLECTION
    flt = None
    conditions = list(must or [])
    if conditions or must_not:
        flt = qm.Filter(must=conditions, must_not=list(must_not or []))
    resp = get_client().query_points(
        collection_name=coll,
        query=[float(x) for x in vector],
        limit=limit,
        score_threshold=score_threshold,
        query_filter=flt,
        with_payload=True,
        with_vectors=False,
    )
    return list(resp.points)


def scroll_segment_points(
    segment_id: str, with_vectors: bool = False, name: str | None = None
) -> list[qm.Record]:
    """Fetch points belonging to one segment (used by Find Similar)."""
    coll = name or config.QDRANT_COLLECTION
    records, _ = get_client().scroll(
        collection_name=coll,
        scroll_filter=qm.Filter(
            must=[qm.FieldCondition(key="segment_id", match=qm.MatchValue(value=segment_id))]
        ),
        limit=64,
        with_payload=True,
        with_vectors=with_vectors,
    )
    return records


def count_points(name: str | None = None) -> int:
    coll = name or config.QDRANT_COLLECTION
    return int(get_client().count(coll, exact=True).count)


def iter_all_vectors(batch: int = 256) -> Iterable[qm.Record]:
    """Scroll every point with its vector (Explore / Find Similar helpers)."""
    offset = None
    while True:
        records, offset = get_client().scroll(
            collection_name=config.QDRANT_COLLECTION,
            limit=batch,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        for r in records:
            yield r
        if offset is None:
            return
