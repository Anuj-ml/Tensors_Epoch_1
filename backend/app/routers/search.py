"""Semantic search endpoints (AI Brain, Find Similar, history restore).

Endpoint family per ARCHITECTURE §12 (route style follows this app's
conventions: /api/search for text, /api/search/{kind} for the rest).
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..ids import ms_to_clock
from ..services import clip, observability, visualstore
from ..services.search import (
    SearchFilters,
    find_similar,
    image_search,
    restore_search,
    semantic_search,
)

router = APIRouter(prefix="/api", tags=["search"])


class FiltersModel(BaseModel):
    language: str | None = None
    duration_ms_min: int | None = None
    duration_ms_max: int | None = None
    min_score: float | None = None

    def to_filters(self) -> SearchFilters:
        return SearchFilters(language=self.language,
                             duration_ms_min=self.duration_ms_min,
                             duration_ms_max=self.duration_ms_max,
                             min_score=self.min_score)


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=100)
    filters: FiltersModel | None = None


class SimilarRequest(BaseModel):
    segment_id: str = Field(min_length=1)
    top_k: int = Field(default=10, ge=1, le=100)
    mode: str = Field(default="semantic", pattern="^(semantic|visual|hybrid)$")


class ImageSearchRequest(BaseModel):
    # data-URL ("data:image/png;base64,...") or raw base64 — no multipart dep.
    image_base64: str = Field(min_length=8, max_length=80_000_000)
    filename: str | None = Field(default=None, max_length=255)
    top_k: int = Field(default=10, ge=1, le=100)
    filters: FiltersModel | None = None


def _search_text(req: SearchRequest, query_type: str) -> dict:
    f = req.filters or FiltersModel()
    try:
        return semantic_search(
            req.query, top_k=req.top_k, filters=f.to_filters(), query_type=query_type,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — Qdrant/model failure surfaced cleanly
        raise HTTPException(status_code=503, detail=f"search unavailable: {exc}") from exc


@router.post("/search")
def post_search(req: SearchRequest) -> dict:
    return _search_text(req, "text")


@router.post("/search/text")
def post_search_text(req: SearchRequest) -> dict:
    """POST /api/search/text (§12 alias — same retrieval path)."""
    return _search_text(req, "text")


@router.post("/search/similar")
def post_search_similar(req: SimilarRequest) -> dict:
    """POST /api/search/similar — Find Similar with visual/hybrid modes (§6)."""
    try:
        return find_similar(req.segment_id, top_k=req.top_k, mode=req.mode)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"search unavailable: {exc}") from exc


@router.post("/search/find-similar")
def post_find_similar(req: SimilarRequest) -> dict:
    return post_search_similar(req)


class ScriptSearchRequest(BaseModel):
    script: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=6, ge=1, le=20)


@router.post("/search/script")
def post_search_script(req: ScriptSearchRequest) -> dict:
    """POST /api/search/script — beat-level Script-to-B-Roll retrieval (§12)."""
    from .script import AnalyzeRequest, analyze  # local import avoids a cycle

    return analyze(AnalyzeRequest(script=req.script, top_k=req.top_k))


@router.post("/search/image")
def post_search_image(req: ImageSearchRequest) -> dict:
    """POST /api/search/image — reference image → CLIP → timestamped scenes (§6)."""
    try:
        raw = clip.image_from_base64(req.image_base64)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    status = visualstore.status()
    if not status.get("indexed"):
        raise HTTPException(
            status_code=503,
            detail="CLIP frame index is empty — run: python worker/pipeline.py visual",
        )
    f = req.filters or FiltersModel()
    try:
        return image_search(raw, top_k=req.top_k, filters=f.to_filters(),
                            filename=req.filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"image search unavailable: {exc}") from exc


@router.get("/search/{search_id}")
def get_search_alias(search_id: str) -> dict:
    """GET /api/search/{search_id} (§12 alias of /api/searches/{id})."""
    restored = restore_search(search_id)
    if not restored:
        raise HTTPException(status_code=404, detail="search not found")
    observability.log_event("search.reopened", {"search_id": search_id})
    return restored


@router.get("/searches")
def list_searches(limit: int = 50, saved_only: bool = False) -> dict:
    conn = db.connect()
    try:
        sql = ("SELECT search_id, query, query_type, result_count, is_saved, saved_label, "
               "created_at FROM searches")
        if saved_only:
            sql += " WHERE is_saved=1"
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        rows = conn.execute(sql, (min(limit, 200),)).fetchall()
        return {"searches": [
            {**{k: r[k] for k in ("search_id", "query", "query_type", "result_count",
                                  "is_saved", "saved_label", "created_at")},
             "is_saved": bool(r["is_saved"])}
            for r in rows
        ]}
    finally:
        conn.close()


@router.get("/searches/{search_id}")
def get_search(search_id: str) -> dict:
    restored = restore_search(search_id)
    if not restored:
        raise HTTPException(status_code=404, detail="search not found")
    return restored


@router.post("/searches/{search_id}/save")
def save_search(search_id: str, label: str | None = None) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT search_id FROM searches WHERE search_id=?",
                           (search_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="search not found")
        conn.execute("UPDATE searches SET is_saved=1, saved_label=COALESCE(?, saved_label) "
                     "WHERE search_id=?", (label, search_id))
        conn.commit()
        return {"search_id": search_id, "is_saved": True}
    finally:
        conn.close()


@router.delete("/searches/{search_id}/save")
def unsave_search(search_id: str) -> dict:
    conn = db.connect()
    try:
        conn.execute("UPDATE searches SET is_saved=0 WHERE search_id=?", (search_id,))
        conn.commit()
        return {"search_id": search_id, "is_saved": False}
    finally:
        conn.close()


@router.get("/segments/{segment_id}")
def get_segment(segment_id: str, annotation_limit: int = 50) -> dict:
    conn = db.connect()
    try:
        row = conn.execute(
            """SELECT s.*, v.status AS video_status FROM segments s
               JOIN videos v ON v.video_id = s.video_id WHERE s.segment_id=?""",
            (segment_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="segment not found")
        anns = conn.execute(
            """SELECT annotation_id, annotation_number, description_original,
                      declared_language, detected_language, description_english,
                      translation_status, source, is_duplicate
               FROM annotations WHERE segment_id=? AND status='active'
               ORDER BY annotation_number LIMIT ?""",
            (segment_id, min(annotation_limit, 200)),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) c FROM annotations WHERE segment_id=? AND status='active'",
            (segment_id,),
        ).fetchone()["c"]
        detections = conn.execute(
            "SELECT type, label, confidence, bbox_x, bbox_y, bbox_w, bbox_h, source "
            "FROM detections WHERE segment_id=? ORDER BY confidence DESC LIMIT 50",
            (segment_id,),
        ).fetchall()
        emb = conn.execute(
            "SELECT model_name, model_version, modality, vector_version, dim "
            "FROM embedding_meta WHERE segment_id=?",
            (segment_id,),
        ).fetchall()
        return {
            "segment_id": row["segment_id"],
            "video_id": row["video_id"],
            "start_ms": row["start_ms"],
            "end_ms": row["end_ms"],
            "start_clock": ms_to_clock(row["start_ms"]),
            "end_clock": ms_to_clock(row["end_ms"]),
            "duration_ms": row["duration_ms"],
            "file_name": row["file_name"],
            "status": row["status"],
            "width": row["width"],
            "height": row["height"],
            "fps": row["fps"],
            "codec": row["codec"],
            "sha256": row["sha256"],
            "thumbnail_url": f"/media/thumbnails/{segment_id.replace(':', '_').replace('-', '__')}.jpg"
            if row["thumbnail_path"] else None,
            "preview_endpoint": f"/api/segments/{segment_id}/preview",
            "annotation_count": total,
            "annotations": [
                {**{k: a[k] for k in ("annotation_id", "annotation_number",
                                      "description_original", "declared_language",
                                      "detected_language", "description_english",
                                      "translation_status", "source")},
                 "is_duplicate": bool(a["is_duplicate"])}
                for a in anns
            ],
            "detections": [dict(d) for d in detections],
            "embeddings": [dict(e) for e in emb],
        }
    finally:
        conn.close()
