"""Semantic + visual retrieval — embeddings + Qdrant only.

No LIKE / contains / keyword / TF-IDF / filename matching anywhere in this
module. Results are grouped back to segments so one segment never appears
multiple times just because several of its annotations matched.

Ranking (ARCHITECTURE §7):
    final score = weighted (vector similarity, query-concept overlap,
    metadata/filter match) — raw components stay visible per result in
    `score_components`, plus a normalized `relevance` (0-100) for the card.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from qdrant_client.http import models as qm

from .. import config, db
from ..ids import ms_to_clock
from . import clip, concepts, embeddings, observability, vectorstore, visualstore

_MODEL_CACHE: dict[str, str] = {}


def _text_model() -> str:
    if "text" not in _MODEL_CACHE:
        _MODEL_CACHE["text"] = config.EMBEDDING_MODEL
    return _MODEL_CACHE["text"]


@dataclass
class SearchFilters:
    language: str | None = None          # annotation language code
    duration_ms_min: int | None = None
    duration_ms_max: int | None = None
    min_score: float | None = None

    def qdrant_conditions(self) -> list[qm.Condition]:
        out: list[qm.Condition] = []
        if self.language:
            out.append(qm.FieldCondition(
                key="detected_language", match=qm.MatchValue(value=self.language)))
        if self.duration_ms_min is not None or self.duration_ms_max is not None:
            rng: dict[str, float] = {}
            if self.duration_ms_min is not None:
                rng["gte"] = self.duration_ms_min
            if self.duration_ms_max is not None:
                rng["lte"] = self.duration_ms_max
            out.append(qm.FieldCondition(key="duration_ms", range=qm.Range(**rng)))
        return out

    def active(self) -> dict:
        return {
            k: v for k, v in (
                ("language", self.language),
                ("duration_ms_min", self.duration_ms_min),
                ("duration_ms_max", self.duration_ms_max),
                ("min_score", self.min_score),
            )
            if v is not None
        }


def _duration_conditions(filters: SearchFilters) -> list[qm.Condition]:
    """Duration-only conditions for CLIP payloads (no language key there)."""
    if filters.duration_ms_min is None and filters.duration_ms_max is None:
        return []
    rng: dict[str, float] = {}
    if filters.duration_ms_min is not None:
        rng["gte"] = filters.duration_ms_min
    if filters.duration_ms_max is not None:
        rng["lte"] = filters.duration_ms_max
    return [qm.FieldCondition(key="duration_ms", range=qm.Range(**rng))]


def _segment_rows(conn, segment_ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(segment_ids), 400):
        chunk = segment_ids[i:i + 400]
        ph = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""SELECT s.segment_id, s.video_id, s.start_ms, s.end_ms, s.duration_ms,
                       s.file_name, s.file_path, s.thumbnail_path, s.status,
                       s.width, s.height, s.fps, v.status AS video_status
                FROM segments s JOIN videos v ON v.video_id = s.video_id
                WHERE s.segment_id IN ({ph})""",
            chunk,
        ).fetchall()
        for r in rows:
            out[r["segment_id"]] = dict(r)
    return out


def _group_and_rank(hits: list[qm.ScoredPoint], top_k: int,
                    excluded: set[str] | None = None, source: str = "text") -> list[dict]:
    """Collapse annotation-level hits into segment-level results."""
    excluded = excluded or set()
    grouped: dict[str, dict] = {}
    for hit in hits:
        pl = hit.payload or {}
        seg = pl.get("segment_id")
        if not seg or seg in excluded:
            continue
        score = float(hit.score)
        entry = grouped.get(seg)
        ann = {
            "annotation_id": pl.get("annotation_id"),
            "description": pl.get("description_english") or pl.get("description_original"),
            "description_original": pl.get("description_original"),
            "language": pl.get("detected_language"),
            "score": score,
        }
        if entry is None:
            grouped[seg] = {
                "segment_id": seg,
                "score": score,
                "matched_annotations": [ann],
                "video_id": pl.get("video_id"),
                "start_ms": pl.get("start_ms"),
                "end_ms": pl.get("end_ms"),
                "duration_ms": pl.get("duration_ms"),
                "video_filename": pl.get("video_filename"),
                "thumbnail_path": pl.get("thumbnail_path"),
                "source": source,
            }
        else:
            entry["score"] = max(entry["score"], score)
            if len(entry["matched_annotations"]) < 5:
                entry["matched_annotations"].append(ann)
    ranked = sorted(grouped.values(), key=lambda e: e["score"], reverse=True)
    return ranked[:top_k]


def _group_frame_hits(hits: list[qm.ScoredPoint], top_k: int,
                      excluded: set[str] | None = None) -> list[dict]:
    """Collapse CLIP frame-level hits into segment-level results (§6 image search)."""
    excluded = excluded or set()
    grouped: dict[str, dict] = {}
    for hit in hits:
        pl = hit.payload or {}
        seg = pl.get("segment_id")
        if not seg or seg in excluded:
            continue
        score = float(hit.score)
        frame = {
            "frame_index": pl.get("frame_index"),
            "frame_offset_ms": pl.get("frame_offset_ms"),
            "frame_path": pl.get("frame_path"),
            "score": round(score, 4),
        }
        entry = grouped.get(seg)
        if entry is None:
            grouped[seg] = {
                "segment_id": seg,
                "score": score,
                "matched_annotations": [],
                "frame": frame,
                "video_id": pl.get("video_id"),
                "start_ms": pl.get("start_ms"),
                "end_ms": pl.get("end_ms"),
                "duration_ms": pl.get("duration_ms"),
                "video_filename": pl.get("video_filename"),
                "thumbnail_path": pl.get("thumbnail_path"),
                "source": "clip",
            }
        elif score > entry["score"]:
            entry["score"] = score
            entry["frame"] = frame
    ranked = sorted(grouped.values(), key=lambda e: e["score"], reverse=True)
    return ranked[:top_k]


def _rrf(*lists: list[dict]) -> list[dict]:
    """Reciprocal Rank Fusion for hybrid retrieval (rank-based, calibration-free)."""
    scores: dict[str, float] = {}
    best: dict[str, dict] = {}
    for lst in lists:
        for rank, entry in enumerate(lst, start=1):
            sid = entry["segment_id"]
            scores[sid] = scores.get(sid, 0.0) + 1.0 / (60 + rank)
            prev = best.get(sid)
            if prev is None or entry["score"] > prev["score"]:
                best[sid] = entry
    fused: list[dict] = []
    for sid, s in sorted(scores.items(), key=lambda kv: -kv[1]):
        e = dict(best[sid])
        e["rrf_score"] = round(s, 5)
        fused.append(e)
    return fused


def _hydrate(conn, ranked: list[dict], query: str,
             filters_applied: bool = False, sort: bool = True) -> list[dict]:
    """Attach DB metadata + explanation + ranking score to ranked segments.

    Description lookup falls back to SQLite for visual/CLIP hits (their Qdrant
    payloads carry no annotation text by design — §14 payload rules).
    """
    ids = [r["segment_id"] for r in ranked]
    if not ids:
        return []
    meta = _segment_rows(conn, ids)
    query_concepts = (
        {c.lower() for c in concepts.extract_concepts(query, limit=8)} if query else set()
    )

    missing = [
        r["segment_id"] for r in ranked
        if not any(a.get("description") for a in r.get("matched_annotations") or [])
    ]
    desc_fallback: dict[str, Any] = {}
    if missing:
        for i in range(0, len(missing), 400):
            chunk = missing[i:i + 400]
            ph = ",".join("?" * len(chunk))
            for row in conn.execute(
                f"""SELECT segment_id, annotation_id, description_english,
                           description_original, detected_language
                    FROM annotations
                    WHERE segment_id IN ({ph}) AND status='active' AND is_duplicate=0
                      AND (description_english IS NOT NULL OR description_original IS NOT NULL)
                    ORDER BY segment_id, annotation_number""",
                chunk,
            ):
                desc_fallback.setdefault(row["segment_id"], row)

    out: list[dict] = []
    for r in ranked:
        m = meta.get(r["segment_id"])
        if not m or m["status"] != "ready" or not m["file_path"]:
            continue
        best = r["matched_annotations"][0] if r["matched_annotations"] else {}
        desc = best.get("description") or ""
        language = best.get("language")
        if not desc:
            fb = desc_fallback.get(r["segment_id"])
            if fb:
                desc = fb["description_english"] or fb["description_original"] or ""
                language = fb["detected_language"]
                if not r["matched_annotations"]:
                    r["matched_annotations"] = [{
                        "annotation_id": fb["annotation_id"],
                        "description": desc,
                        "description_original": fb["description_original"],
                        "language": language,
                        "score": float(r["score"]),
                    }]
        result_concepts = concepts.extract_concepts(desc, limit=6) if desc else []
        shared = [c for c in result_concepts if c.lower() in query_concepts]

        # ---- ranking components (§7) --------------------------------------
        vector = float(r["score"])
        overlap = (len(shared) / max(len(query_concepts), 1)) if query_concepts else 0.0
        weights = {"vector_similarity": 0.7, "concept_overlap": 0.2,
                   "metadata_filter": 0.1}
        present = {"vector_similarity": vector}
        if query_concepts:
            present["concept_overlap"] = overlap
        if filters_applied:
            present["metadata_filter"] = 1.0
        den = sum(weights[k] for k in present)
        final = sum(weights[k] * present[k] for k in present) / den if den else vector
        final = round(min(max(final, 0.0), 1.0), 4)
        components = {k: round(v, 4) for k, v in present.items()}
        relevance = int(round(final * 100))

        start_ms, end_ms = int(m["start_ms"]), int(m["end_ms"])
        item = {
            "segment_id": r["segment_id"],
            "video_id": m["video_id"],
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start_clock": ms_to_clock(start_ms),
            "end_clock": ms_to_clock(end_ms),
            "duration_ms": r.get("duration_ms") or m["duration_ms"],
            "score": final,
            "vector_score": round(vector, 4),
            "relevance": relevance,
            "score_components": components,
            "source": r.get("source", "text"),
            "description": desc,
            "description_original": best.get("description_original"),
            "language": language,
            "why_match": shared or result_concepts[:4],
            "concepts": result_concepts,
            "matched_annotations": r["matched_annotations"][:3],
            "matched_annotation_count": len(r["matched_annotations"]),
            "video_filename": m["file_name"],
            "thumbnail_url": f"/media/thumbnails/{_thumb_name(r['segment_id'])}" if m["thumbnail_path"] else None,
            "preview_endpoint": f"/api/segments/{r['segment_id']}/preview",
            "width": m["width"],
            "height": m["height"],
            "fps": m["fps"],
        }
        if r.get("frame"):
            item["frame"] = r["frame"]
        if r.get("rrf_score") is not None:
            item["score_components"]["rrf"] = r["rrf_score"]
        out.append(item)
    if sort:
        out.sort(key=lambda x: x["score"], reverse=True)
    return out


def _thumb_name(segment_id: str) -> str:
    return segment_id.replace(":", "_").replace("-", "__") + ".jpg"


def _persist(conn, search_id: str, query: str, query_type: str, params: dict,
             results: list[dict]) -> None:
    conn.execute(
        "INSERT INTO searches(search_id, query, query_type, params, result_count) "
        "VALUES(?,?,?,?,?)",
        (search_id, query, query_type, json.dumps(params, ensure_ascii=False),
         len(results)),
    )
    conn.executemany(
        "INSERT INTO search_results(search_id, segment_id, score, rank, annotation_ids, why_match) "
        "VALUES(?,?,?,?,?,?)",
        [
            (
                search_id, r["segment_id"], r["score"], i + 1,
                json.dumps([a["annotation_id"] for a in r["matched_annotations"]]),
                json.dumps({
                    "concepts": r["concepts"],
                    "why_match": r["why_match"],
                    "relevance": r["relevance"],
                    "score_components": r["score_components"],
                    "source": r["source"],
                }, ensure_ascii=False),
            )
            for i, r in enumerate(results)
        ],
    )
    conn.commit()


def semantic_search(
    query: str,
    top_k: int = 10,
    filters: SearchFilters | None = None,
    query_type: str = "text",
    persist: bool = True,
    extra_params: dict | None = None,
) -> dict[str, Any]:
    """Text → MiniLM vectors → Qdrant, fused with CLIP text→frame results
    for scenes that have no text annotations (uploaded videos, §2/§6)."""
    started = time.perf_counter()
    query = (query or "").strip()
    if not query:
        raise ValueError("query must not be empty")
    top_k = max(1, min(int(top_k), config.MAX_TOP_K))
    filters = filters or SearchFilters()

    vector = embeddings.embed_query(query)
    must = filters.qdrant_conditions()
    hits = vectorstore.search(
        vector,
        limit=top_k * config.SEARCH_OVERFETCH,
        score_threshold=filters.min_score,
        must=must or None,
    )
    ranked = _group_and_rank(hits, top_k * 3)

    # ---- CLIP text→frame fusion (only when a visual index exists) ---------
    clip_candidates = 0
    try:
        if visualstore.has_points():
            tvec = clip.encode_text(query)
            chits = visualstore.search(
                tvec,
                limit=top_k * config.SEARCH_OVERFETCH,
                score_threshold=filters.min_score,
                must=_duration_conditions(filters) or None,
            )
            present = {g["segment_id"] for g in ranked}
            clip_only = [
                g for g in _group_frame_hits(chits, top_k * 3)
                if g["segment_id"] not in present
            ]
            clip_candidates = len(clip_only)
            ranked = ranked + clip_only[: max(top_k, 10)]
    except Exception:  # noqa: BLE001 — visual index is optional for text search
        clip_candidates = 0

    search_id = str(uuid.uuid4())
    conn = db.connect()
    try:
        results = _hydrate(conn, ranked, query, filters_applied=bool(must))
        results = results[:top_k]
        params = {
            "top_k": top_k,
            "language": filters.language,
            "duration_ms_min": filters.duration_ms_min,
            "duration_ms_max": filters.duration_ms_max,
            "min_score": filters.min_score,
            "clip_candidates": clip_candidates,
            **(extra_params or {}),
        }
        if persist:
            _persist(conn, search_id, query, query_type, params, results)
    finally:
        conn.close()

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    if persist:
        observability.log_search(
            search_id, query_type, _text_model(),
            candidate_count=len(ranked),
            top_scores=[r["score"] for r in results],
            filters=filters.active(), latency_ms=latency_ms,
            result_count=len(results),
        )
    return {
        "search_id": search_id,
        "query": query,
        "query_type": query_type,
        "understanding": concepts.describe_query(query, filters=filters.active()),
        "results": results,
        "result_count": len(results),
        "candidates_grouped": len(ranked),
        "latency_ms": latency_ms,
        "score_note": ("score = weighted relevance (vector similarity + concept "
                       "overlap + filter match; see score_components)"),
    }


def image_search(
    image_bytes: bytes,
    top_k: int = 10,
    filters: SearchFilters | None = None,
    persist: bool = True,
    filename: str | None = None,
) -> dict[str, Any]:
    """Reference image → CLIP image embedding → scene vectors → timestamps (§6)."""
    started = time.perf_counter()
    top_k = max(1, min(int(top_k), config.MAX_TOP_K))
    filters = filters or SearchFilters()
    info = clip.model_info()
    vector = clip.encode_image_bytes(image_bytes)[0]
    must = _duration_conditions(filters)

    hits = visualstore.search(
        vector,
        limit=top_k * config.SEARCH_OVERFETCH,
        score_threshold=filters.min_score,
        must=must or None,
    )
    ranked = _group_frame_hits(hits, top_k * 3)

    search_id = str(uuid.uuid4())
    conn = db.connect()
    try:
        results = _hydrate(conn, ranked, "", filters_applied=bool(must))
        results = results[:top_k]
        params = {
            "top_k": top_k,
            "duration_ms_min": filters.duration_ms_min,
            "duration_ms_max": filters.duration_ms_max,
            "min_score": filters.min_score,
            "source": "reference_image",
            "filename": filename,
            "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
            "model": info["model"],
            "dim": info["dim"],
        }
        if persist:
            _persist(conn, search_id, "[image]", "image", params, results)
    finally:
        conn.close()

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    if persist:
        observability.log_event("image_search.created", {
            "search_id": search_id, "filename": filename,
            "result_count": len(results),
        })
        observability.log_search(
            search_id, "image", info["model"],
            candidate_count=len(ranked),
            top_scores=[r["score"] for r in results],
            filters=filters.active(), latency_ms=latency_ms,
            result_count=len(results),
        )
    return {
        "search_id": search_id,
        "query": f"[image] {filename or ''}".strip(),
        "query_type": "image",
        "understanding": {
            "source": "reference_image",
            "model": info["model"],
            "concepts": [],
            "filters": filters.active(),
        },
        "results": results,
        "result_count": len(results),
        "candidates_grouped": len(ranked),
        "latency_ms": latency_ms,
        "score_note": ("score = weighted relevance (visual similarity; see "
                       "score_components)"),
    }


def find_similar(segment_id: str, top_k: int = 10, persist: bool = True,
                 mode: str = "semantic") -> dict[str, Any]:
    """Selected scene embedding → Qdrant → similar scenes (§6 Find Similar).

    mode="semantic" — MiniLM description vectors (pure text nearest neighbour)
    mode="visual"   — CLIP frame vectors (pure visual nearest neighbour)
    mode="hybrid"   — reciprocal-rank fusion of both when both exist
    """
    if mode not in {"semantic", "visual", "hybrid"}:
        raise ValueError("mode must be one of: semantic, visual, hybrid")
    started = time.perf_counter()
    top_k = max(1, min(int(top_k), config.MAX_TOP_K))

    limit = max(top_k, 10) * config.SEARCH_OVERFETCH
    exclude_cond = [qm.FieldCondition(
        key="segment_id", match=qm.MatchValue(value=segment_id))]

    sem_ranked: list[dict] = []
    anchor_desc: str | None = None
    ann_vectors = 0
    records = vectorstore.scroll_segment_points(segment_id, with_vectors=True)
    if records:
        import numpy as np

        vectors = [np.asarray(r.vector, dtype="float32") for r in records if r.vector]
        query_vec = np.mean(np.vstack(vectors), axis=0)
        norm = float(np.linalg.norm(query_vec))
        if norm > 1e-9:
            query_vec = query_vec / norm
        hits = vectorstore.search(query_vec, limit=limit, must_not=exclude_cond)
        sem_ranked = _group_and_rank(hits, top_k * 3, excluded={segment_id})
        pl = records[0].payload or {}
        anchor_desc = pl.get("description_english") or pl.get("description_original")
        ann_vectors = len(records)

    vis_ranked: list[dict] = []
    frame_vectors = 0
    if visualstore.has_points():
        frame_vec = visualstore.segment_vector(segment_id)
        if frame_vec is not None:
            vh = visualstore.search(frame_vec, limit=limit, must_not=exclude_cond)
            vis_ranked = _group_frame_hits(vh, top_k * 3, excluded={segment_id})
            frame_vectors = len(visualstore.segment_vectors(segment_id))

    if mode == "semantic" and not sem_ranked:
        raise KeyError(f"no indexed vectors for segment {segment_id}")
    if mode == "visual" and not vis_ranked:
        raise KeyError(
            f"no visual frames indexed for segment {segment_id} "
            "— run `python worker/pipeline.py visual` first"
        )

    if mode == "semantic":
        ranked, keep_order = sem_ranked, True
    elif mode == "visual":
        ranked, keep_order = vis_ranked, True
    else:
        sides = [s for s in (sem_ranked, vis_ranked) if s]
        if not sides:
            raise KeyError(f"no indexed vectors for segment {segment_id}")
        ranked, keep_order = (_rrf(*sides), False) if len(sides) > 1 else (sides[0], True)

    search_id = str(uuid.uuid4())
    conn = db.connect()
    try:
        if not anchor_desc:
            row = conn.execute(
                """SELECT description_english, description_original FROM annotations
                   WHERE segment_id=? AND status='active' ORDER BY annotation_number LIMIT 1""",
                (segment_id,),
            ).fetchone()
            if row:
                anchor_desc = row["description_english"] or row["description_original"]
        results = _hydrate(conn, ranked, anchor_desc or "", sort=keep_order)
        results = results[:top_k]
        if persist:
            _persist(conn, search_id, f"find_similar:{segment_id}", "similar",
                     {"segment_id": segment_id, "top_k": top_k, "mode": mode},
                     results)
    finally:
        conn.close()

    latency_ms = round((time.perf_counter() - started) * 1000, 1)
    if persist:
        observability.log_event(
            "scene.find_similar",
            {"segment_id": segment_id, "mode": mode, "result_count": len(results)},
        )
        observability.log_search(
            search_id, "similar", _text_model(),
            candidate_count=len(ranked),
            top_scores=[r["score"] for r in results],
            filters={"mode": mode}, latency_ms=latency_ms,
            result_count=len(results),
        )

    return {
        "search_id": search_id if persist else None,
        "query": f"find similar to {segment_id}",
        "query_type": "similar",
        "mode": mode,
        "latency_ms": latency_ms,
        "anchor": {
            "segment_id": segment_id,
            "description": anchor_desc,
            "annotation_vectors": ann_vectors,
            "frame_vectors": frame_vectors,
        },
        "results": results,
        "result_count": len(results),
        "score_note": ("score = weighted relevance (vector similarity + concept "
                       "overlap; see score_components)"),
    }


def restore_search(search_id: str) -> dict | None:
    """Load a previous search + its stored results without re-running retrieval."""
    conn = db.connect()
    try:
        s = conn.execute("SELECT * FROM searches WHERE search_id=?", (search_id,)).fetchone()
        if not s:
            return None
        rows = conn.execute(
            "SELECT * FROM search_results WHERE search_id=? ORDER BY rank", (search_id,)
        ).fetchall()
        ids = [r["segment_id"] for r in rows]
        meta = _segment_rows(conn, ids) if ids else {}
        results = []
        for r in rows:
            m = meta.get(r["segment_id"])
            if not m:
                continue
            why = json.loads(r["why_match"] or "{}")
            ann_ids = json.loads(r["annotation_ids"] or "[]")
            best = conn.execute(
                "SELECT description_original, description_english, detected_language "
                "FROM annotations WHERE annotation_id=?",
                (ann_ids[0],),
            ).fetchone() if ann_ids else None
            desc = (best["description_english"] or best["description_original"]) if best else None
            if desc is None:
                fb = conn.execute(
                    """SELECT description_english, description_original FROM annotations
                       WHERE segment_id=? AND status='active' AND is_duplicate=0
                       ORDER BY annotation_number LIMIT 1""",
                    (r["segment_id"],),
                ).fetchone()
                if fb:
                    desc = fb["description_english"] or fb["description_original"]
            item = {
                "segment_id": r["segment_id"],
                "video_id": m["video_id"],
                "start_ms": m["start_ms"],
                "end_ms": m["end_ms"],
                "start_clock": ms_to_clock(m["start_ms"]),
                "end_clock": ms_to_clock(m["end_ms"]),
                "duration_ms": m["duration_ms"],
                "score": r["score"],
                "relevance": why.get("relevance",
                                     int(round(float(r["score"]) * 100))),
                "score_components": why.get("score_components", {}),
                "source": why.get("source", "text"),
                "description": desc,
                "why_match": why.get("why_match", []),
                "concepts": why.get("concepts", []),
                "video_filename": m["file_name"],
                "thumbnail_url": f"/media/thumbnails/{_thumb_name(r['segment_id'])}",
                "preview_endpoint": f"/api/segments/{r['segment_id']}/preview",
            }
            results.append(item)
        return {
            "search_id": s["search_id"],
            "query": s["query"],
            "query_type": s["query_type"],
            "params": json.loads(s["params"] or "{}"),
            "created_at": s["created_at"],
            "is_saved": bool(s["is_saved"]),
            "results": results,
            "result_count": len(results),
            "score_note": ("score = weighted relevance (vector similarity + concept "
                           "overlap; see score_components)"),
        }
    finally:
        conn.close()
