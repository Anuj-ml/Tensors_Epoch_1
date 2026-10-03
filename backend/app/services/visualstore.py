"""CLIP frame-vector store — the vision half of Qdrant (ARCHITECTURE §6/§14).

Rules enforced here:
- Writes only into config.CLIP_COLLECTION with CLIP-dim vectors; the text
  path never touches this collection and vice versa.
- Existing collections are never deleted/recreated; dim mismatch raises
  (vectorstore.ensure_collection does the enforcing).
- Payloads stay small: scene IDs, timestamps, frame pointers — no blobs.
"""
from __future__ import annotations

import uuid
from typing import Any, Sequence

import numpy as np
from qdrant_client.http import models as qm

from .. import config
from ..ids import NAMESPACE
from . import vectorstore


def collection_name() -> str:
    return config.CLIP_COLLECTION


def frame_point_id(segment_id: str, frame_index: int) -> str:
    """Stable uuid5 point id — idempotent re-indexing."""
    return str(uuid.uuid5(NAMESPACE, f"frame|{segment_id}|{frame_index}"))


def ensure_collection(dim: int) -> dict[str, Any]:
    if config.CLIP_COLLECTION == config.QDRANT_COLLECTION:
        raise RuntimeError(
            "CLIP_COLLECTION must differ from QDRANT_COLLECTION "
            "(512-dim vision vs 384-dim text)"
        )
    return vectorstore.ensure_collection(dim, name=config.CLIP_COLLECTION)


def upsert_frames(
    ids: Sequence[str],
    vectors: Sequence[Sequence[float]],
    payloads: Sequence[dict[str, Any]],
) -> None:
    vectorstore.upsert(ids, vectors, payloads, name=config.CLIP_COLLECTION)


def search(
    vector: Sequence[float] | np.ndarray,
    limit: int = 10,
    score_threshold: float | None = None,
    must: Sequence[qm.FieldCondition] | None = None,
    must_not: Sequence[qm.FieldCondition] | None = None,
) -> list[qm.ScoredPoint]:
    return vectorstore.search(
        [float(x) for x in vector],
        limit=limit,
        score_threshold=score_threshold,
        must=must,
        must_not=must_not,
        name=config.CLIP_COLLECTION,
    )


def _count(must: list[qm.FieldCondition] | None = None) -> int:
    client = vectorstore.get_client()
    flt = qm.Filter(must=must) if must else None
    resp = client.count(
        collection_name=config.CLIP_COLLECTION,
        count_filter=flt,
        exact=True,
    )
    return int(resp.count)


def count_points() -> int:
    return _count()


def has_points() -> bool:
    """True when at least one frame vector is indexed (cheap guard)."""
    try:
        return _count() > 0
    except Exception:  # noqa: BLE001 — collection missing = no visual index
        return False


def has_segment(segment_id: str) -> bool:
    try:
        return _count([qm.FieldCondition(
            key="segment_id", match=qm.MatchValue(value=segment_id))]) > 0
    except Exception:  # noqa: BLE001
        return False


def segment_vectors(segment_id: str) -> list[np.ndarray]:
    records = vectorstore.scroll_segment_points(
        segment_id, with_vectors=True, name=config.CLIP_COLLECTION
    )
    return [np.asarray(r.vector, dtype="float32") for r in records if r.vector]


def segment_vector(segment_id: str) -> np.ndarray | None:
    """Pooled scene vector (mean of frame vectors) for Find Similar (§6)."""
    vecs = segment_vectors(segment_id)
    if not vecs:
        return None
    mean = np.mean(np.vstack(vecs), axis=0)
    norm = float(np.linalg.norm(mean))
    if norm < 1e-9:
        return None
    return (mean / norm).astype(np.float32)


def delete_video_frames(video_id: str) -> int:
    """Remove all frame points for one video (idempotent re-indexing, §15)."""
    return _delete_where("video_id", video_id)


def delete_segment_frames(segment_id: str) -> int:
    """Remove frame points for one segment (refresh re-indexing, §15)."""
    return _delete_where("segment_id", segment_id)


def _delete_where(key: str, value: str) -> int:
    client = vectorstore.get_client()
    records, _ = client.scroll(
        collection_name=config.CLIP_COLLECTION,
        scroll_filter=qm.Filter(must=[qm.FieldCondition(
            key=key, match=qm.MatchValue(value=value))]),
        limit=4096,
        with_payload=False,
        with_vectors=False,
    )
    ids = [r.id for r in records]
    if ids:
        client.delete(
            collection_name=config.CLIP_COLLECTION,
            points_selector=qm.PointIdsList(points=ids),
            wait=True,
        )
    return len(ids)


def status() -> dict[str, Any]:
    """Health snapshot for /api/health — never raises (§16)."""
    out: dict[str, Any] = {
        "provider": "clip",
        "model": config.CLIP_MODEL,
        "collection": config.CLIP_COLLECTION,
        "reachable": False,
        "points": 0,
        "dim": None,
    }
    try:
        if not vectorstore.ping():
            return out
        names = {c.name for c in vectorstore.get_client().get_collections().collections}
        out["reachable"] = True
        if config.CLIP_COLLECTION in names:
            info = vectorstore.collection_info(config.CLIP_COLLECTION)
            out["points"] = int(info["points"] or 0)
            out["dim"] = info["dim"]
            out["indexed"] = out["points"] > 0
        else:
            out["indexed"] = False
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:200]
    return out
