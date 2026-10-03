"""History — searches, opened scenes, saved searches, selected clips.

History is event metadata, not a media store (ARCHITECTURE §10): events
reference IDs; large payloads stay out of the table.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db

router = APIRouter(prefix="/api/history", tags=["history"])

# §10 recommended event names (legacy client names kept for compatibility).
ALLOWED_EVENTS = {
    "search.created", "search.reopened",
    "scene.opened", "scene.selected", "scene.added_to_story", "scene.find_similar",
    "image_search.created", "rough_cut.generated",
    # legacy names still accepted from older clients
    "opened_scene", "selected_clip", "opened_explore_point", "image_search",
}


class EventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=50)
    payload: dict = Field(default_factory=dict)


class SavedSearchCreate(BaseModel):
    search_id: str = Field(min_length=1)
    label: str | None = Field(default=None, max_length=200)


def _overview(days: int) -> dict:
    conn = db.connect()
    try:
        searches = conn.execute(
            """SELECT search_id, query, query_type, result_count, is_saved, saved_label,
                      created_at FROM searches
               WHERE created_at >= datetime('now', ?)
               ORDER BY created_at DESC, rowid DESC LIMIT 200""",
            (f"-{max(1, min(days, 365))} days",),
        ).fetchall()
        saved = conn.execute(
            "SELECT search_id, query, query_type, result_count, saved_label, created_at "
            "FROM searches WHERE is_saved=1 ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
        events = conn.execute(
            """SELECT event_type, COUNT(*) c FROM history_events
               WHERE created_at >= datetime('now', ?) GROUP BY event_type""",
            (f"-{max(1, min(days, 365))} days",),
        ).fetchall()
        clips = conn.execute(
            """SELECT c.story_clip_id, c.project_id, c.segment_id, p.name AS project_name,
                      c.created_at FROM story_clips c
               LEFT JOIN story_projects p ON p.project_id = c.project_id
               ORDER BY c.created_at DESC LIMIT 100"""
        ).fetchall()
        return {
            "searches": [
                {**{k: s[k] for k in ("search_id", "query", "query_type", "result_count",
                                      "saved_label", "created_at")},
                 "is_saved": bool(s["is_saved"])}
                for s in searches
            ],
            "saved_searches": [
                {**{k: s[k] for k in ("search_id", "query", "query_type",
                                      "result_count", "saved_label", "created_at")}}
                for s in saved
            ],
            "event_counts": {e["event_type"]: e["c"] for e in events},
            "recent_clips": [dict(c) for c in clips],
        }
    finally:
        conn.close()


@router.get("/overview")
def overview(days: int = 30) -> dict:
    return _overview(days)


@router.get("")
def history_root(days: int = Query(30, ge=1, le=365)) -> dict:
    """GET /api/history (§12 — alias of /api/history/overview)."""
    return _overview(days)


@router.get("/events")
def list_events(limit: int = 100) -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM history_events ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (min(limit, 500),),
        ).fetchall()
        return {"events": [
            {"id": r["id"], "event_type": r["event_type"],
             "payload": json.loads(r["payload"] or "{}"), "created_at": r["created_at"]}
            for r in rows
        ]}
    finally:
        conn.close()


@router.post("/events")
def create_event(req: EventCreate) -> dict:
    if req.event_type not in ALLOWED_EVENTS:
        raise HTTPException(
            status_code=400,
            detail=f"event_type must be one of {sorted(ALLOWED_EVENTS)}",
        )
    conn = db.connect()
    try:
        cur = conn.execute(
            "INSERT INTO history_events(event_type, payload) VALUES(?,?)",
            (req.event_type, json.dumps(req.payload, ensure_ascii=False)),
        )
        conn.commit()
        return {"id": cur.lastrowid, "event_type": req.event_type, "stored": True}
    finally:
        conn.close()


@router.post("/saved")
def save_search(req: SavedSearchCreate) -> dict:
    """POST /api/history/saved (§12 — named saved search, not a result copy)."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT search_id FROM searches WHERE search_id=?",
                           (req.search_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="search not found")
        conn.execute(
            "UPDATE searches SET is_saved=1, saved_label=COALESCE(?, saved_label) "
            "WHERE search_id=?",
            (req.label, req.search_id),
        )
        conn.commit()
        return {"search_id": req.search_id, "is_saved": True}
    finally:
        conn.close()


@router.delete("/saved/{search_id}")
def delete_saved_search(search_id: str) -> dict:
    """DELETE /api/history/saved/{id} (§12 — remove the saved flag/label)."""
    conn = db.connect()
    try:
        cur = conn.execute(
            "UPDATE searches SET is_saved=0, saved_label=NULL WHERE search_id=? "
            "AND is_saved=1",
            (search_id,),
        )
        conn.commit()
        if cur.rowcount == 0:
            exists = conn.execute("SELECT 1 FROM searches WHERE search_id=?",
                                  (search_id,)).fetchone()
            if not exists:
                raise HTTPException(status_code=404, detail="search not found")
        return {"search_id": search_id, "is_saved": False}
    finally:
        conn.close()
