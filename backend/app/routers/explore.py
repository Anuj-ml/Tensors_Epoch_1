"""Explore — legacy flat points + Visual Content Universe (cluster cache)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from .. import db
from ..ids import ms_to_clock

router = APIRouter(prefix="/api/explore", tags=["explore"])

UNIVERSE_FILE = Path(__file__).resolve().parents[3] / "data" / "explore" / "universe_v1.json"
_cache_lock = threading.Lock()
_cache: dict[str, Any] | None = None
_cache_mtime: float = 0.0
_detail_cache: dict[str, tuple[float, list[dict]]] = {}


# ------------------------------------------------------------------ universe cache
def _load_universe(refresh: bool = False) -> dict:
    global _cache, _cache_mtime
    if not UNIVERSE_FILE.exists() or refresh:
        from worker.stages import universe  # type: ignore

        try:
            universe.run(refresh=refresh)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503,
                                detail=f"universe unavailable: {exc}") from exc
    with _cache_lock:
        mtime = UNIVERSE_FILE.stat().st_mtime
        if _cache is None or mtime != _cache_mtime:
            _cache = json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))
            _cache_mtime = mtime
        return _cache


def _universe_summary(doc: dict) -> dict:
    """Serve hierarchy + points without the big centroid vectors."""
    galaxies = []
    for g in doc["galaxies"]:
        galaxies.append({k: v for k, v in g.items() if k != "centroid"} | {
            "subclusters": [{k: v for k, v in s.items() if k != "centroid"}
                            for s in g["subclusters"]]
        })
    return {
        "version": doc["version"],
        "model": doc["model"],
        "count": doc["count"],
        "galaxy_count": doc["galaxy_count"],
        "subcluster_count": doc["subcluster_count"],
        "silhouette": doc["silhouette"],
        "chosen_k": doc["chosen_k"],
        "created_at": doc["created_at"],
        "generator": doc["generator"],
        "galaxies": galaxies,
        "points": doc["points"],
    }


@router.get("/universe")
def universe(refresh: bool = Query(False)) -> dict:
    return _universe_summary(_load_universe(refresh=refresh))


def _galaxy_points(doc: dict, gid: str) -> list[dict]:
    mtime = UNIVERSE_FILE.stat().st_mtime
    hit = _detail_cache.get(gid)
    if hit and hit[0] == mtime:
        return hit[1]

    if not gid.startswith("g") or not gid[1:].isdigit():
        raise HTTPException(status_code=404, detail=f"unknown galaxy: {gid}")
    gidx = int(gid[1:])
    ids = [p["id"] for p in doc["points"] if p["g"] == gidx]
    if not ids:
        raise HTTPException(status_code=404, detail=f"unknown galaxy: {gid}")

    placeholders = ",".join("?" * len(ids))
    conn = db.connect()
    try:
        rows = conn.execute(
            f"""SELECT s.segment_id, s.video_id, s.start_ms, s.end_ms, s.duration_ms,
                       s.file_name, s.thumbnail_path, s.status,
                       (SELECT description_english FROM annotations a
                         WHERE a.segment_id = s.segment_id AND a.status='active'
                           AND a.is_duplicate = 0 AND a.description_english IS NOT NULL
                         ORDER BY a.annotation_number LIMIT 1) AS description
                FROM segments s WHERE s.segment_id IN ({placeholders})""",
            ids,
        ).fetchall()
    finally:
        conn.close()
    by_id = {r["segment_id"]: r for r in rows}
    pos = {p["id"]: p for p in doc["points"] if p["id"] in by_id}

    out = []
    for seg in ids:
        r = by_id.get(seg)
        p = pos.get(seg)
        if not r or not p:
            continue
        out.append({
            "segment_id": seg,
            "x": p["x"], "y": p["y"], "z": p["z"], "s": p["s"],
            "start_ms": r["start_ms"],
            "end_ms": r["end_ms"],
            "start_clock": ms_to_clock(r["start_ms"]),
            "end_clock": ms_to_clock(r["end_ms"]),
            "duration_ms": r["duration_ms"],
            "description": r["description"],
            "video_filename": r["file_name"],
            "thumbnail_url": (f"/media/thumbnails/{seg.replace(':', '_').replace('-', '__')}.jpg"
                              if r["thumbnail_path"] else None),
            "preview_endpoint": f"/api/segments/{seg}/preview",
        })
    _detail_cache[gid] = (mtime, out)
    return out


@router.get("/universe/galaxies/{gid}")
def universe_galaxy(gid: str) -> dict:
    doc = _load_universe()
    g = next((x for x in doc["galaxies"] if x["id"] == gid), None)
    if g is None:
        raise HTTPException(status_code=404, detail=f"unknown galaxy: {gid}")
    return {
        "galaxy": {k: v for k, v in g.items() if k != "centroid"},
        "points": _galaxy_points(doc, gid),
    }


# ------------------------------------------------------------------ highlight
class HighlightIn(BaseModel):
    query: str
    top_k: int = 30


def _cos(a: np.ndarray, b: np.ndarray) -> float:
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


@router.post("/universe/highlight")
def universe_highlight(body: HighlightIn) -> dict:
    if not body.query.strip():
        raise HTTPException(status_code=422, detail="empty query")
    from ..services import embeddings, vectorstore

    doc = _load_universe()
    try:
        vec = np.asarray(embeddings.embed_query(body.query), dtype=np.float32)
        hits = vectorstore.search(vec, limit=body.top_k)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"highlight unavailable: {exc}") from exc

    galaxies, subs = [], []
    for g in doc["galaxies"]:
        gc = np.asarray(g["centroid"], dtype=np.float32)
        galaxies.append({"id": g["id"], "score": round(_cos(vec, gc), 4)})
        for s in g["subclusters"]:
            sc = np.asarray(s["centroid"], dtype=np.float32)
            subs.append({"id": s["id"], "score": round(_cos(vec, sc), 4)})
    galaxies.sort(key=lambda x: -x["score"])
    subs.sort(key=lambda x: -x["score"])

    return {
        "query": body.query,
        "galaxies": galaxies[:10],
        "subclusters": subs[:15],
        "segments": [{"segment_id": h.payload.get("segment_id"), "score": round(h.score, 4)}
                     for h in hits if h.payload.get("segment_id")],
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


# ------------------------------------------------------------------ scene detail (§12)
@router.get("/scene/{segment_id}")
def explore_scene(segment_id: str) -> dict:
    """Scene identity + map position + metadata for the Explore viewport (§9)."""
    conn = db.connect()
    try:
        row = conn.execute(
            """SELECT s.*, v.status AS video_status FROM segments s
               JOIN videos v ON v.video_id = s.video_id WHERE s.segment_id=?""",
            (segment_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="scene not found")
        anns = conn.execute(
            """SELECT annotation_id, description_original, description_english,
                      detected_language FROM annotations
               WHERE segment_id=? AND status='active' ORDER BY annotation_number LIMIT 5""",
            (segment_id,),
        ).fetchall()
        pos_row = conn.execute(
            "SELECT x, y, model FROM explore_points WHERE segment_id=?",
            (segment_id,),
        ).fetchone()
    finally:
        conn.close()

    # Position from the cached universe projection (no recompute on request, §9).
    coords: dict[str, Any] | None = None
    try:
        doc = _load_universe()
        for p in doc["points"]:
            if p["id"] == segment_id:
                coords = {"x": p["x"], "y": p["y"], "z": p.get("z"),
                          "galaxy": f"g{p['g']:02d}", "sub": p.get("s")}
                g = next((x for x in doc["galaxies"] if x["id"] == coords["galaxy"]), None)
                if g:
                    coords["galaxy_label"] = g.get("label")
                break
    except Exception:  # noqa: BLE001 — universe cache optional for scene detail
        coords = None
    if coords is None and pos_row is not None:
        coords = {"x": pos_row["x"], "y": pos_row["y"], "model": pos_row["model"]}

    return {
        "segment_id": row["segment_id"],
        "video_id": row["video_id"],
        "start_ms": row["start_ms"],
        "end_ms": row["end_ms"],
        "start_clock": ms_to_clock(row["start_ms"]),
        "end_clock": ms_to_clock(row["end_ms"]),
        "duration_ms": row["duration_ms"],
        "video_filename": row["file_name"],
        "status": row["status"],
        "coordinates": coords,
        "thumbnail_url": (f"/media/thumbnails/"
                          f"{segment_id.replace(':', '_').replace('-', '__')}.jpg"
                          if row["thumbnail_path"] else None),
        "preview_endpoint": f"/api/segments/{segment_id}/preview",
        "find_similar": {"endpoint": "/api/search/similar",
                         "segment_id": segment_id},
        "annotations": [
            {"annotation_id": a["annotation_id"],
             "description": a["description_english"] or a["description_original"],
             "language": a["detected_language"]}
            for a in anns
        ],
    }


# ------------------------------------------------------------------ legacy flat map
@router.get("/points")
def points(refresh: bool = Query(False)) -> dict:
    db.init_db()
    conn = db.connect()
    try:
        cached = conn.execute("SELECT COUNT(*) c FROM explore_points").fetchone()["c"]
    finally:
        conn.close()

    computed = None
    if cached == 0 or refresh:
        from worker.stages.explore import compute  # type: ignore

        try:
            computed = compute(refresh=refresh or cached == 0)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"explore unavailable: {exc}") from exc

    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT p.segment_id, p.x, p.y, s.video_id, s.start_ms, s.end_ms,
                      s.duration_ms, s.file_name, s.thumbnail_path, s.status,
                      (SELECT description_english FROM annotations a
                        WHERE a.segment_id = p.segment_id AND a.status='active'
                          AND a.is_duplicate = 0 AND a.description_english IS NOT NULL
                        ORDER BY a.annotation_number LIMIT 1) AS description
               FROM explore_points p JOIN segments s ON s.segment_id = p.segment_id
               WHERE s.status='ready'"""
        ).fetchall()
        xs = [r["x"] for r in rows] or [0.0]
        ys = [r["y"] for r in rows] or [0.0]
        return {
            "count": len(rows),
            "bounds": {"min_x": min(xs), "max_x": max(xs),
                       "min_y": min(ys), "max_y": max(ys)},
            "computed": computed,
            "points": [
                {
                    "segment_id": r["segment_id"],
                    "x": r["x"],
                    "y": r["y"],
                    "video_id": r["video_id"],
                    "start_ms": r["start_ms"],
                    "end_ms": r["end_ms"],
                    "duration_ms": r["duration_ms"],
                    "description": r["description"],
                    "video_filename": r["file_name"],
                    "thumbnail_url": (f"/media/thumbnails/"
                                      f"{r['segment_id'].replace(':', '_').replace('-', '__')}.jpg"
                                      if r["thumbnail_path"] else None),
                    "preview_endpoint": f"/api/segments/{r['segment_id']}/preview",
                }
                for r in rows
            ],
        }
    finally:
        conn.close()
