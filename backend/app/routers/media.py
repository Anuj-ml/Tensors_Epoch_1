"""Media endpoints — thumbnails, browser previews, rough cuts (FFmpeg)."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import config, db

router = APIRouter(prefix="/api", tags=["media"])


def _segment_file(segment_id: str) -> tuple[Path, Path | None]:
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT file_path, thumbnail_path, status FROM segments WHERE segment_id=?",
            (segment_id,),
        ).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="segment not found")
    if not row["file_path"] or not Path(row["file_path"]).is_file():
        raise HTTPException(status_code=410, detail="source file missing on disk")
    thumb = Path(row["thumbnail_path"]) if row["thumbnail_path"] else None
    return Path(row["file_path"]), thumb


@router.get("/segments/{segment_id}/thumbnail")
def thumbnail(segment_id: str) -> FileResponse:
    _, thumb = _segment_file(segment_id)
    if not thumb or not thumb.exists():
        raise HTTPException(status_code=404, detail="thumbnail not generated yet")
    return FileResponse(thumb, media_type="image/jpeg")


@router.post("/segments/{segment_id}/preview")
def create_preview(segment_id: str) -> dict:
    """Ensure an MP4 preview exists (transcodes on first request) and return its URL."""
    from worker.stages.previews import get_or_create_preview  # type: ignore

    _segment_file(segment_id)
    try:
        out = get_or_create_preview(segment_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"preview failed: {exc}") from exc
    return {
        "segment_id": segment_id,
        "url": f"/media/previews/{out.name}",
        "ready": True,
    }


@router.get("/segments/{segment_id}/preview/status")
def preview_status(segment_id: str) -> dict:
    from worker.stages.previews import preview_path_for

    path = preview_path_for(segment_id)
    return {"segment_id": segment_id, "ready": path.exists(),
            "url": f"/media/previews/{path.name}" if path.exists() else None}
