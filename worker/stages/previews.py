"""Browser-playable preview generation (MP4/H.264 via FFmpeg).

Source clips are AVI which browsers cannot play; previews are derived media
stored under media/previews/. Originals are never modified.
"""
from __future__ import annotations

import subprocess
import threading
from pathlib import Path

from app import config, db

_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(segment_id: str) -> threading.Lock:
    with _locks_guard:
        lock = _locks.get(segment_id)
        if lock is None:
            lock = threading.Lock()
            _locks[segment_id] = lock
        return lock


def preview_path_for(segment_id: str) -> Path:
    safe = segment_id.replace(":", "_").replace("-", "__")
    return config.PREVIEW_DIR / f"{safe}.mp4"


def transcode(file_path: Path, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.mp4")
    cmd = [
        "ffmpeg", "-y", "-i", str(file_path),
        "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(tmp),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0 or not tmp.exists():
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg preview failed: {proc.stderr.strip()[-400:]}")
    tmp.replace(out_path)


def get_or_create_preview(segment_id: str) -> Path:
    """Return the preview mp4, transcoding on first request (thread-safe)."""
    conn = db.connect()
    try:
        row = conn.execute(
            "SELECT file_path, preview_path FROM segments WHERE segment_id=?",
            (segment_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None or not row["file_path"]:
        raise FileNotFoundError(f"segment {segment_id} has no source file")
    out = Path(row["preview_path"]) if row["preview_path"] else preview_path_for(segment_id)
    with _lock_for(segment_id):
        if out.exists() and out.stat().st_size > 0:
            return out
        transcode(Path(row["file_path"]), out)
        conn = db.connect()
        try:
            conn.execute(
                "UPDATE segments SET preview_path=? WHERE segment_id=?",
                (str(out), segment_id),
            )
            conn.commit()
        finally:
            conn.close()
    return out


def run(limit: int | None = None, progress=None) -> dict:
    db.init_db()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT segment_id FROM segments WHERE status='ready' AND file_path IS NOT NULL "
            "ORDER BY segment_id"
        ).fetchall()
    finally:
        conn.close()
    if limit:
        rows = rows[:limit]
    ok = failed = skipped = 0
    failures: list[dict] = []
    total = len(rows)
    for i, row in enumerate(rows):
        seg = row["segment_id"]
        out = preview_path_for(seg)
        if out.exists() and out.stat().st_size > 0:
            skipped += 1
            continue
        try:
            get_or_create_preview(seg)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            failures.append({"segment": seg, "error": str(exc)[:300]})
        if progress and i % 10 == 0:
            progress((i + 1) / max(total, 1), f"previews {i + 1}/{total}")
    return {"created": ok, "already_present": skipped, "failed": failed,
            "failures": failures[:50]}
