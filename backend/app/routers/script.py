"""Script-to-B-Roll — split a script into visual beats, search per beat."""
from __future__ import annotations

import json
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..ids import ms_to_clock, uuid_name
from ..services import concepts
from ..services.search import SearchFilters, semantic_search

router = APIRouter(prefix="/api/script", tags=["script"])

MAX_BEATS = 16
MAX_BEAT_CHARS = 220


class AnalyzeRequest(BaseModel):
    script: str = Field(min_length=1, max_length=8000)
    top_k: int = Field(default=6, ge=1, le=20)


class SelectRequest(BaseModel):
    segment_id: str = Field(min_length=1)


def split_beats(script: str) -> list[str]:
    """Sentence/clause segmentation → independent visual beats (real text ops)."""
    text = re.sub(r"\s+", " ", (script or "").strip())
    if not text:
        return []
    parts: list[str] = []
    for para in re.split(r"(?<=[.!?])\s+|\n+", text):
        para = para.strip()
        if not para:
            continue
        if len(para) > MAX_BEAT_CHARS:
            chunks = re.split(r"(?<=[;:])\s+|,\s+(?=(?:and|then|while|as)\s)", para)
            buf = ""
            for ch in chunks:
                ch = ch.strip()
                if not ch:
                    continue
                if buf and len(buf) + len(ch) + 1 > MAX_BEAT_CHARS:
                    parts.append(buf.strip(" ,;"))
                    buf = ch
                else:
                    buf = f"{buf} {ch}".strip()
            if buf:
                parts.append(buf.strip(" ,;"))
        else:
            parts.append(para)
    beats: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if beats and len(p.split()) <= 2 and len(beats[-1]) < MAX_BEAT_CHARS:
            beats[-1] = f"{beats[-1]} {p}"
        else:
            beats.append(p)
    return beats[:MAX_BEATS]


def _visual_intent(beat_text: str) -> str:
    cs = concepts.extract_concepts(beat_text, limit=5)
    return " · ".join(cs) if cs else beat_text[:60]


def _slim(result: dict) -> dict:
    return {
        "segment_id": result["segment_id"],
        "score": result["score"],
        "description": result["description"],
        "start_ms": result["start_ms"],
        "end_ms": result["end_ms"],
        "start_clock": result["start_clock"],
        "end_clock": result["end_clock"],
        "duration_ms": result["duration_ms"],
        "video_filename": result["video_filename"],
        "thumbnail_url": result["thumbnail_url"],
        "preview_endpoint": result["preview_endpoint"],
        "concepts": result["concepts"],
    }


@router.post("/analyze")
def analyze(req: AnalyzeRequest) -> dict:
    beat_texts = split_beats(req.script)
    if not beat_texts:
        raise HTTPException(status_code=400, detail="no beats could be derived from script")

    analysis_id = uuid_name("analysis", req.script)
    conn = db.connect()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO script_analyses(analysis_id, script_text, status) "
            "VALUES(?,?,?)",
            (analysis_id, req.script, "running"),
        )
        conn.execute("DELETE FROM script_beats WHERE analysis_id=?", (analysis_id,))
        conn.commit()
    finally:
        conn.close()

    beats_out = []
    try:
        for idx, text in enumerate(beat_texts):
            search = semantic_search(
                text, top_k=req.top_k, filters=SearchFilters(),
                query_type="script", persist=False,
                extra_params={"analysis_id": analysis_id, "beat_index": idx},
            )
            candidates = [_slim(r) for r in search["results"]]
            beat_id = uuid_name("beat", f"{analysis_id}|{idx}")
            intent = _visual_intent(text)
            conn = db.connect()
            try:
                conn.execute(
                    """INSERT INTO script_beats(beat_id, analysis_id, beat_index, text,
                       visual_intent, candidates) VALUES(?,?,?,?,?,?)""",
                    (beat_id, analysis_id, idx, text, intent,
                     json.dumps(candidates, ensure_ascii=False)),
                )
                conn.commit()
            finally:
                conn.close()
            beats_out.append({
                "beat_id": beat_id,
                "beat_index": idx,
                "text": text,
                "visual_intent": intent,
                "selected_segment_id": None,
                "candidates": candidates,
            })
    except Exception as exc:  # noqa: BLE001
        conn = db.connect()
        try:
            conn.execute("UPDATE script_analyses SET status='failed' WHERE analysis_id=?",
                         (analysis_id,))
            conn.commit()
        finally:
            conn.close()
        raise HTTPException(status_code=503, detail=f"beat search failed: {exc}") from exc

    conn = db.connect()
    try:
        conn.execute("UPDATE script_analyses SET status='done' WHERE analysis_id=?",
                     (analysis_id,))
        conn.commit()
    finally:
        conn.close()

    return {
        "analysis_id": analysis_id,
        "script": req.script,
        "status": "done",
        "beat_count": len(beats_out),
        "beats": beats_out,
    }


def _analysis_payload(analysis_id: str) -> dict:
    conn = db.connect()
    try:
        a = conn.execute("SELECT * FROM script_analyses WHERE analysis_id=?",
                         (analysis_id,)).fetchone()
        if not a:
            raise HTTPException(status_code=404, detail="analysis not found")
        rows = conn.execute(
            "SELECT * FROM script_beats WHERE analysis_id=? ORDER BY beat_index",
            (analysis_id,),
        ).fetchall()
        return {
            "analysis_id": a["analysis_id"],
            "script": a["script_text"],
            "status": a["status"],
            "created_at": a["created_at"],
            "beat_count": len(rows),
            "beats": [
                {
                    "beat_id": r["beat_id"],
                    "beat_index": r["beat_index"],
                    "text": r["text"],
                    "visual_intent": r["visual_intent"],
                    "selected_segment_id": r["selected_segment_id"],
                    "candidates": json.loads(r["candidates"] or "[]"),
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


@router.get("/analyses")
def list_analyses() -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT analysis_id, script_text, status, created_at FROM script_analyses "
            "ORDER BY created_at DESC, rowid DESC LIMIT 50"
        ).fetchall()
        return {"analyses": [
            {"analysis_id": r["analysis_id"],
             "script": r["script_text"][:200],
             "status": r["status"], "created_at": r["created_at"]}
            for r in rows
        ]}
    finally:
        conn.close()


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: str) -> dict:
    return _analysis_payload(analysis_id)


@router.post("/beats/{beat_id}/select")
def select_segment(beat_id: str, req: SelectRequest) -> dict:
    conn = db.connect()
    try:
        beat = conn.execute("SELECT * FROM script_beats WHERE beat_id=?",
                            (beat_id,)).fetchone()
        if not beat:
            raise HTTPException(status_code=404, detail="beat not found")
        seg = conn.execute("SELECT status FROM segments WHERE segment_id=?",
                           (req.segment_id,)).fetchone()
        if not seg or seg["status"] != "ready":
            raise HTTPException(status_code=404, detail="segment not available")
        conn.execute("UPDATE script_beats SET selected_segment_id=? WHERE beat_id=?",
                     (req.segment_id, beat_id))
        conn.commit()
        return {"beat_id": beat_id, "selected_segment_id": req.segment_id}
    finally:
        conn.close()
