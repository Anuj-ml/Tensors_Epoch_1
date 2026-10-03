"""Video upload + indexing endpoints (ARCHITECTURE §5/§12/§15).

Uploads stream as raw bytes with an X-Filename header (no multipart
dependency), are validated by extension + MIME + size, stored under a
controlled uuid key, and indexed by a background job that never blocks the
API thread. Ingestion is idempotent: same bytes → same video_id.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import config, db
from ..ids import uuid_name

router = APIRouter(prefix="/api", tags=["videos"])

ALLOWED_UPLOAD_MIME = {
    "video/mp4", "video/x-msvideo", "video/avi", "video/quicktime",
    "video/webm", "video/x-matroska", "video/mpeg", "video/x-m4v",
    "application/octet-stream",
}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class IndexRequest(BaseModel):
    force: bool = False


def _sanitize_filename(raw: str) -> str:
    """Path-traversal-safe basename (§15: never trust client filenames)."""
    name = Path(raw.replace("\\", "/")).name          # drops directories
    name = _SAFE_NAME.sub("_", name).lstrip("._")
    if not name or len(name) > 200:
        raise HTTPException(status_code=400, detail="invalid filename")
    return name


def _validate_upload(filename: str, content_type: str | None, size: int) -> None:
    ext = Path(filename).suffix.lower()
    if ext not in config.ALLOWED_VIDEO_EXTS:
        raise HTTPException(
            status_code=415,
            detail=f"unsupported file type {ext or '(none)'} "
                   f"— allowed: {sorted(config.ALLOWED_VIDEO_EXTS)}",
        )
    if not content_type or content_type.split(";")[0].strip().lower() not in ALLOWED_UPLOAD_MIME:
        raise HTTPException(status_code=415,
                            detail=f"unsupported content type: {content_type!r}")
    if size <= 0:
        raise HTTPException(status_code=400, detail="empty upload")
    if size > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"upload exceeds {config.MAX_UPLOAD_BYTES} bytes")


async def read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and int(declared) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413,
                            detail=f"upload exceeds {config.MAX_UPLOAD_BYTES} bytes")
    buf = bytearray()
    async for chunk in request.stream():
        buf.extend(chunk)
        if len(buf) > config.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413,
                                detail=f"upload exceeds {config.MAX_UPLOAD_BYTES} bytes")
    return bytes(buf)


@router.post("/videos")
async def upload_video(request: Request) -> dict:
    """POST /api/videos — store an immutable source video (§12/§14/§15)."""
    filename = request.headers.get("x-filename")
    if not filename:
        raise HTTPException(status_code=400, detail="X-Filename header is required")
    safe_name = _sanitize_filename(filename)
    content_type = request.headers.get("content-type")

    body = await read_body(request)
    _validate_upload(safe_name, content_type, len(body))
    sha = hashlib.sha256(body).hexdigest()

    # Idempotency (§15): identical bytes return the existing video.
    conn = db.connect()
    try:
        existing = conn.execute("SELECT * FROM videos WHERE sha256=? AND file_path IS NOT NULL",
                                (sha,)).fetchone()
        if existing and existing["file_path"] and Path(existing["file_path"]).is_file():
            return {
                "video_id": existing["video_id"],
                "status": existing["status"],
                "filename": existing["original_name"],
                "size_bytes": len(body),
                "sha256": sha,
                "existing": True,
                "index_endpoint": f"/api/videos/{existing['video_id']}/index",
                "detail_endpoint": f"/api/videos/{existing['video_id']}",
            }
        video_id = uuid_name("upload", sha)
        dest_dir = config.UPLOAD_DIR / video_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / safe_name
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(body)
        tmp.replace(dest)                     # source media is immutable once stored
        conn.execute(
            """INSERT INTO videos(video_id, status, file_count, sha256,
                                  original_name, file_path)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(video_id) DO UPDATE SET
                 status=excluded.status, original_name=excluded.original_name,
                 file_path=excluded.file_path, sha256=excluded.sha256""",
            (video_id, "uploaded", 1, sha, safe_name, str(dest)),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "video_id": video_id,
        "status": "uploaded",
        "filename": safe_name,
        "size_bytes": len(body),
        "sha256": sha,
        "existing": False,
        "index_endpoint": f"/api/videos/{video_id}/index",
        "detail_endpoint": f"/api/videos/{video_id}",
    }


@router.post("/videos/{video_id}/index")
def index_video(video_id: str, req: IndexRequest | None = None) -> dict:
    """POST /api/videos/{id}/index — probe → shots → thumbnails → CLIP → Qdrant."""
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()
        if not row or not row["file_path"]:
            raise HTTPException(status_code=404, detail="uploaded video not found")
        if not Path(row["file_path"]).is_file():
            raise HTTPException(status_code=410, detail="source file missing on disk")
        if row["status"] == "ready" and not (req and req.force):
            segs = conn.execute(
                "SELECT COUNT(*) c FROM segments WHERE video_id=?", (video_id,)
            ).fetchone()["c"]
            return {"video_id": video_id, "status": "ready", "job_id": None,
                    "segments": segs, "note": "already indexed"}
        job_id = f"index-{video_id}"
        st = conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if st and st["status"] in {"queued", "running"}:
            return {"video_id": video_id, "status": "indexing", "job_id": job_id,
                    "status_endpoint": f"/api/jobs/{job_id}"}
        # finished job from an earlier attempt — replace it (idempotent re-run)
        conn.execute("DELETE FROM jobs WHERE job_id=?", (job_id,))
        conn.execute(
            "INSERT INTO jobs(job_id, job_type, status, progress, detail) VALUES(?,?,?,?,?)",
            (job_id, "video_index", "queued", 0.0,
             json.dumps({"video_id": video_id, "force": bool(req and req.force)})),
        )
        conn.execute("UPDATE videos SET status='indexing' WHERE video_id=?", (video_id,))
        conn.commit()
    finally:
        conn.close()

    def _run() -> None:
        from worker.stages.uploads import index_uploaded  # type: ignore

        try:
            index_uploaded(video_id, job_id=job_id,
                           force=bool(req and req.force))
        except Exception:  # noqa: BLE001 — job row records the failure
            pass

    threading.Thread(target=_run, daemon=True).start()
    return {"video_id": video_id, "status": "indexing", "job_id": job_id,
            "status_endpoint": f"/api/jobs/{job_id}"}


@router.get("/videos")
def list_videos(limit: int = 50) -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT video_id, status, original_name, file_path, sha256, duration_ms,
                      created_at FROM videos
               WHERE file_path IS NOT NULL
               ORDER BY created_at DESC LIMIT ?""",
            (min(limit, 200),),
        ).fetchall()
        out = []
        for r in rows:
            segs = conn.execute(
                "SELECT COUNT(*) c FROM segments WHERE video_id=? AND status='ready'",
                (r["video_id"],),
            ).fetchone()["c"]
            out.append({
                "video_id": r["video_id"], "status": r["status"],
                "filename": r["original_name"], "size_bytes": None,
                "duration_ms": r["duration_ms"], "segments": segs,
                "sha256": r["sha256"], "created_at": r["created_at"],
                "detail_endpoint": f"/api/videos/{r['video_id']}",
            })
        return {"videos": out}
    finally:
        conn.close()


@router.get("/videos/{video_id}")
def get_video(video_id: str) -> dict:
    conn = db.connect()
    try:
        row = conn.execute("SELECT * FROM videos WHERE video_id=?", (video_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="video not found")
        segs = conn.execute(
            """SELECT segment_id, start_ms, end_ms, duration_ms, status, thumbnail_path
               FROM segments WHERE video_id=? ORDER BY start_ms""",
            (video_id,),
        ).fetchall()
        return {
            "video_id": row["video_id"],
            "status": row["status"],
            "filename": row["original_name"],
            "file_path": row["file_path"],
            "sha256": row["sha256"],
            "duration_ms": row["duration_ms"],
            "created_at": row["created_at"],
            "segments": [
                {"segment_id": s["segment_id"], "start_ms": s["start_ms"],
                 "end_ms": s["end_ms"], "duration_ms": s["duration_ms"],
                 "status": s["status"],
                 "thumbnail_url": (f"/media/thumbnails/"
                                   f"{s['segment_id'].replace(':', '_').replace('-', '__')}.jpg"
                                   if s["thumbnail_path"] else None)}
                for s in segs
            ],
        }
    finally:
        conn.close()
