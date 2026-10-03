"""Story / Clip Editor + rough-cut job endpoints."""
from __future__ import annotations

import json
import threading
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..ids import ms_to_clock, uuid_name

router = APIRouter(prefix="/api", tags=["story"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ClipCreate(BaseModel):
    segment_id: str
    source_start_ms: int | None = None
    source_end_ms: int | None = None


class ClipUpdate(BaseModel):
    source_start_ms: int | None = None
    source_end_ms: int | None = None
    order_index: int | None = None


class OrderUpdate(BaseModel):
    clip_ids: list[str]


class RoughCutRequest(BaseModel):
    force: bool = False


def _clip_rows(conn, project_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT c.*, s.video_id, s.start_ms AS seg_start_ms, s.end_ms AS seg_end_ms,
                  s.duration_ms, s.file_name, s.thumbnail_path, s.status
           FROM story_clips c JOIN segments s ON s.segment_id = c.segment_id
           WHERE c.project_id=? ORDER BY c.order_index, c.created_at""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _with_timeline(clips: list[dict]) -> list[dict]:
    from worker.stages.roughcut import timeline  # type: ignore

    return timeline(clips)


def _payload(clip: dict) -> dict:
    dur = clip["source_end_ms"] - clip["source_start_ms"]
    return {
        "story_clip_id": clip["story_clip_id"],
        "project_id": clip["project_id"],
        "segment_id": clip["segment_id"],
        "video_id": clip["video_id"],
        "source_start_ms": clip["source_start_ms"],
        "source_end_ms": clip["source_end_ms"],
        "source_start_clock": ms_to_clock(clip["source_start_ms"]),
        "source_end_clock": ms_to_clock(clip["source_end_ms"]),
        "duration_ms": dur,
        "timeline_start_ms": clip.get("timeline_start_ms", 0),
        "timeline_end_ms": clip.get("timeline_end_ms", 0),
        "order_index": clip["order_index"],
        "description": clip.get("description"),
        "video_filename": clip.get("file_name"),
        "thumbnail_url": (f"/media/thumbnails/"
                          f"{clip['segment_id'].replace(':', '_').replace('-', '__')}.jpg"
                          if clip.get("thumbnail_path") else None),
        "preview_endpoint": f"/api/segments/{clip['segment_id']}/preview",
    }


@router.get("/story/projects")
def list_projects() -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT p.*, COUNT(c.story_clip_id) AS clip_count
               FROM story_projects p LEFT JOIN story_clips c ON c.project_id = p.project_id
               GROUP BY p.project_id ORDER BY p.updated_at DESC"""
        ).fetchall()
        return {"projects": [
            {"project_id": r["project_id"], "name": r["name"],
             "clip_count": r["clip_count"], "created_at": r["created_at"],
             "updated_at": r["updated_at"]}
            for r in rows
        ]}
    finally:
        conn.close()


@router.post("/story/projects")
def create_project(req: ProjectCreate) -> dict:
    pid = uuid_name("project", f"{req.name}-{uuid.uuid4()}")
    conn = db.connect()
    try:
        conn.execute("INSERT INTO story_projects(project_id, name) VALUES(?,?)",
                     (pid, req.name))
        conn.commit()
    finally:
        conn.close()
    return {"project_id": pid, "name": req.name, "clip_count": 0}


@router.get("/story/projects/{project_id}")
def get_project(project_id: str) -> dict:
    conn = db.connect()
    try:
        project = conn.execute(
            "SELECT * FROM story_projects WHERE project_id=?", (project_id,)
        ).fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        clips = _with_timeline(_clip_rows(conn, project_id))
        # best descriptions for display
        ids = [c["segment_id"] for c in clips]
        desc_by_seg: dict[str, str] = {}
        if ids:
            for i in range(0, len(ids), 300):
                chunk = ids[i:i + 300]
                ph = ",".join("?" * len(chunk))
                for r in conn.execute(
                    f"""SELECT segment_id, description_english FROM annotations
                        WHERE segment_id IN ({ph}) AND status='active' AND is_duplicate=0
                          AND description_english IS NOT NULL
                        ORDER BY annotation_number""",
                    chunk,
                ):
                    desc_by_seg.setdefault(r["segment_id"], r["description_english"])
        for c in clips:
            c["description"] = desc_by_seg.get(c["segment_id"])
        return {
            "project_id": project["project_id"],
            "name": project["name"],
            "created_at": project["created_at"],
            "updated_at": project["updated_at"],
            "clips": [_payload(c) for c in clips],
            "total_duration_ms": clips[-1]["timeline_end_ms"] if clips else 0,
        }
    finally:
        conn.close()


@router.delete("/story/projects/{project_id}")
def delete_project(project_id: str) -> dict:
    conn = db.connect()
    try:
        conn.execute("DELETE FROM story_clips WHERE project_id=?", (project_id,))
        cur = conn.execute("DELETE FROM story_projects WHERE project_id=?", (project_id,))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="project not found")
    finally:
        conn.close()
    return {"deleted": project_id}


@router.post("/story/projects/{project_id}/clips")
def add_clip(project_id: str, req: ClipCreate) -> dict:
    conn = db.connect()
    try:
        project = conn.execute("SELECT project_id FROM story_projects WHERE project_id=?",
                               (project_id,)).fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        seg = conn.execute(
            "SELECT start_ms, end_ms, status FROM segments WHERE segment_id=?",
            (req.segment_id,),
        ).fetchone()
        if not seg:
            raise HTTPException(status_code=404, detail="segment not found")
        if seg["status"] != "ready":
            raise HTTPException(status_code=410, detail="segment has no playable file")

        start = req.source_start_ms if req.source_start_ms is not None else seg["start_ms"]
        end = req.source_end_ms if req.source_end_ms is not None else seg["end_ms"]
        start = max(int(start), int(seg["start_ms"]))
        end = min(int(end), int(seg["end_ms"]))
        if end <= start:
            raise HTTPException(status_code=400, detail="invalid trim range")

        order_index = conn.execute(
            "SELECT COALESCE(MAX(order_index), -1) + 1 AS n FROM story_clips "
            "WHERE project_id=?", (project_id,)
        ).fetchone()["n"]
        cid = uuid_name("clip", f"{project_id}|{req.segment_id}|{order_index}|{uuid.uuid4()}")
        conn.execute(
            """INSERT INTO story_clips(story_clip_id, project_id, segment_id,
                 source_start_ms, source_end_ms, timeline_start_ms, timeline_end_ms,
                 order_index) VALUES(?,?,?,?,?,?,?,?)""",
            (cid, project_id, req.segment_id, start, end, 0, 0, order_index),
        )
        conn.execute("UPDATE story_projects SET updated_at=datetime('now') WHERE project_id=?",
                     (project_id,))
        conn.commit()
        clips = _with_timeline(_clip_rows(conn, project_id))
        for c in clips:
            conn.execute(
                "UPDATE story_clips SET timeline_start_ms=?, timeline_end_ms=? "
                "WHERE story_clip_id=?",
                (c["timeline_start_ms"], c["timeline_end_ms"], c["story_clip_id"]),
            )
        conn.commit()
        created = next(c for c in clips if c["story_clip_id"] == cid)
        return _payload(created)
    finally:
        conn.close()


@router.patch("/story/clips/{clip_id}")
def update_clip(clip_id: str, req: ClipUpdate) -> dict:
    conn = db.connect()
    try:
        clip = conn.execute(
            """SELECT c.*, s.start_ms AS seg_start_ms, s.end_ms AS seg_end_ms
               FROM story_clips c JOIN segments s ON s.segment_id=c.segment_id
               WHERE c.story_clip_id=?""",
            (clip_id,),
        ).fetchone()
        if not clip:
            raise HTTPException(status_code=404, detail="clip not found")
        start = req.source_start_ms if req.source_start_ms is not None else clip["source_start_ms"]
        end = req.source_end_ms if req.source_end_ms is not None else clip["source_end_ms"]
        start = max(int(start), int(clip["seg_start_ms"]))
        end = min(int(end), int(clip["seg_end_ms"]))
        if end <= start:
            raise HTTPException(status_code=400, detail="invalid trim range")
        order_index = (req.order_index if req.order_index is not None
                       else clip["order_index"])
        conn.execute(
            "UPDATE story_clips SET source_start_ms=?, source_end_ms=?, order_index=?, "
            "updated_at=datetime('now') WHERE story_clip_id=?",
            (start, end, order_index, clip_id),
        )
        conn.execute("UPDATE story_projects SET updated_at=datetime('now') WHERE project_id=?",
                     (clip["project_id"],))
        conn.commit()
        clips = _with_timeline(_clip_rows(conn, clip["project_id"]))
        for c in clips:
            conn.execute("UPDATE story_clips SET timeline_start_ms=?, timeline_end_ms=? "
                         "WHERE story_clip_id=?",
                         (c["timeline_start_ms"], c["timeline_end_ms"], c["story_clip_id"]))
        conn.commit()
        updated = next(c for c in clips if c["story_clip_id"] == clip_id)
        return _payload(updated)
    finally:
        conn.close()


@router.delete("/story/clips/{clip_id}")
def delete_clip(clip_id: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT project_id FROM story_clips WHERE story_clip_id=?",
                           (clip_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="clip not found")
        conn.execute("DELETE FROM story_clips WHERE story_clip_id=?", (clip_id,))
        remaining = _with_timeline(_clip_rows(conn, row["project_id"]))
        for c in remaining:
            conn.execute("UPDATE story_clips SET timeline_start_ms=?, timeline_end_ms=?, "
                         "order_index=? WHERE story_clip_id=?",
                         (c["timeline_start_ms"], c["timeline_end_ms"],
                          remaining.index(c), c["story_clip_id"]))
        conn.execute("UPDATE story_projects SET updated_at=datetime('now') WHERE project_id=?",
                     (row["project_id"],))
        conn.commit()
    finally:
        conn.close()
    return {"deleted": clip_id}


@router.put("/story/projects/{project_id}/order")
def reorder(project_id: str, req: OrderUpdate) -> dict:
    conn = db.connect()
    try:
        existing = {r["story_clip_id"] for r in conn.execute(
            "SELECT story_clip_id FROM story_clips WHERE project_id=?", (project_id,))}
        if set(req.clip_ids) != existing:
            raise HTTPException(status_code=400, detail="clip_ids must match project clips")
        for idx, cid in enumerate(req.clip_ids):
            conn.execute("UPDATE story_clips SET order_index=?, "
                         "updated_at=datetime('now') WHERE story_clip_id=?",
                         (idx, cid))
        clips = _with_timeline(_clip_rows(conn, project_id))
        for c in clips:
            conn.execute("UPDATE story_clips SET timeline_start_ms=?, timeline_end_ms=? "
                         "WHERE story_clip_id=?",
                         (c["timeline_start_ms"], c["timeline_end_ms"], c["story_clip_id"]))
        conn.execute("UPDATE story_projects SET updated_at=datetime('now') WHERE project_id=?",
                     (project_id,))
        conn.commit()
        return {"project_id": project_id, "order": [c["story_clip_id"] for c in clips]}
    finally:
        conn.close()


@router.post("/story/projects/{project_id}/roughcut")
def generate_roughcut(project_id: str, req: RoughCutRequest) -> dict:
    conn = db.connect()
    try:
        project = conn.execute("SELECT project_id FROM story_projects WHERE project_id=?",
                               (project_id,)).fetchone()
        if not project:
            raise HTTPException(status_code=404, detail="project not found")
        clips = conn.execute("SELECT COUNT(*) c FROM story_clips WHERE project_id=?",
                             (project_id,)).fetchone()["c"]
        if clips == 0:
            raise HTTPException(status_code=400, detail="project has no clips")
        job_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO jobs(job_id, job_type, status, progress, detail) VALUES(?,?,?,?,?)",
            (job_id, "roughcut", "queued", 0.0,
             json.dumps({"project_id": project_id, "clips": clips})),
        )
        conn.commit()
    finally:
        conn.close()

    def _run() -> None:
        from worker.stages.roughcut import render  # type: ignore

        try:
            render(project_id, job_id, force=req.force)
        except Exception:  # noqa: BLE001 — job row already records the failure
            pass

    threading.Thread(target=_run, daemon=True).start()
    return {"job_id": job_id, "status": "queued", "clips": clips,
            "status_endpoint": f"/api/jobs/{job_id}"}


@router.get("/jobs")
def list_jobs(limit: int = 30) -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (min(limit, 200),),
        ).fetchall()
        return {"jobs": [dict(r) | {"detail": json.loads(r["detail"]) if r["detail"] else None}
                         for r in rows]}
    finally:
        conn.close()


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        detail = json.loads(row["detail"]) if row["detail"] else None
        return {**{k: row[k] for k in ("job_id", "job_type", "status", "progress",
                                       "error", "created_at", "updated_at")},
                "detail": detail}
    finally:
        conn.close()
