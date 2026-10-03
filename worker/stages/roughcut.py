"""Phase 15 — Rough Cut generation with FFmpeg.

Takes the ordered story clips (references only — no duplicated media for
basic edits), trims each source segment to its selected range, normalises
scale/fps, and concatenates into an editable MP4 preview.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from app import config, db

TARGET_W, TARGET_H = 1280, 720


def _fetch_clips(project_id: str) -> list[dict]:
    conn = db.connect()
    try:
        project = conn.execute(
            "SELECT * FROM story_projects WHERE project_id=?", (project_id,)
        ).fetchone()
        if not project:
            raise KeyError(f"project {project_id} not found")
        rows = conn.execute(
            """SELECT c.*, s.file_path, s.start_ms AS seg_start_ms, s.end_ms AS seg_end_ms,
                      s.duration_ms AS seg_duration_ms, s.file_name
               FROM story_clips c JOIN segments s ON s.segment_id = c.segment_id
               WHERE c.project_id=? ORDER BY c.order_index, c.created_at""",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def timeline(clips: list[dict]) -> list[dict]:
    """Recompute contiguous timeline positions from clip durations."""
    out = []
    cursor = 0
    for c in clips:
        dur = max(0, c["source_end_ms"] - c["source_start_ms"])
        out.append({**c, "timeline_start_ms": cursor, "timeline_end_ms": cursor + dur,
                    "duration_ms": dur})
        cursor += dur
    return out


def _set_job(job_id: str, status: str, progress: float | None = None,
             error: str | None = None, detail: dict | None = None) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE jobs SET status=?, progress=COALESCE(?, progress), error=?, "
            "detail=COALESCE(?, detail), updated_at=datetime('now') WHERE job_id=?",
            (status, progress, error,
             json.dumps(detail, ensure_ascii=False) if detail is not None else None,
             job_id),
        )
        conn.commit()
    finally:
        conn.close()


def render(project_id: str, job_id: str, force: bool = False) -> dict:
    clips = _fetch_clips(project_id)
    if not clips:
        raise ValueError("project has no clips")
    tl = timeline(clips)

    out_dir = config.ROUGHCUT_DIR / project_id
    out_dir.mkdir(parents=True, exist_ok=True)
    final = out_dir / "roughcut.mp4"
    if final.exists() and not force:
        final.unlink()  # always produce a fresh render for the current timeline

    parts: list[Path] = []
    total = len(tl)
    try:
        for i, clip in enumerate(tl):
            _set_job(job_id, "running", progress=i / (total + 1) * 0.9,
                     detail={"step": f"trimming clip {i + 1}/{total}",
                             "clip": clip["story_clip_id"]})
            src = Path(clip["file_path"])
            if not src or not src.is_file():
                raise FileNotFoundError(f"source missing for clip {clip['story_clip_id']}")
            seg_start = clip["seg_start_ms"]
            local_start = max(0, (clip["source_start_ms"] - seg_start) / 1000.0)
            dur = max(0.1, (clip["source_end_ms"] - clip["source_start_ms"]) / 1000.0)
            part = out_dir / f"part_{i:03d}.mp4"
            vf = (
                f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=decrease,"
                f"pad={TARGET_W}:{TARGET_H}:(ow-iw)/2:(oh-ih)/2,fps=30"
            )
            cmd = [
                "ffmpeg", "-y", "-ss", f"{local_start:.3f}", "-t", f"{dur:.3f}",
                "-i", str(src), "-vf", vf,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-pix_fmt", "yuv420p", "-an", str(part),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0 or not part.exists():
                raise RuntimeError(
                    f"ffmpeg failed on clip {i + 1}: {proc.stderr.strip()[-300:]}"
                )
            parts.append(part)

        _set_job(job_id, "running", progress=0.92, detail={"step": "concatenating"})
        list_file = out_dir / "concat.txt"
        list_file.write_text(
            "".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8"
        )
        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", str(final),
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0 or not final.exists():
            # mixed parameters fallback: re-encode during concat
            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(final),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0 or not final.exists():
                raise RuntimeError(f"concat failed: {proc.stderr.strip()[-300:]}")

        for p in parts:
            p.unlink(missing_ok=True)
        list_file.unlink(missing_ok=True)

        duration_ms = tl[-1]["timeline_end_ms"]
        result = {
            "project_id": project_id,
            "preview_url": f"/media/roughcuts/{project_id}/roughcut.mp4",
            "duration_ms": duration_ms,
            "clips": total,
            "resolution": f"{TARGET_W}x{TARGET_H}",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "editable": True,
        }
        _set_job(job_id, "done", progress=1.0, detail=result)
        return result
    except Exception as exc:  # noqa: BLE001
        for p in parts:
            p.unlink(missing_ok=True)
        _set_job(job_id, "failed", error=str(exc)[:800])
        raise
