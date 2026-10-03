"""Phase 2 — video metadata processing with FFmpeg/ffprobe.

For every segment file: duration, width, height, fps, codec, container,
SHA-256, and a thumbnail. Exact start_ms/end_ms are preserved on the segment.
Runs probes concurrently (subprocess/IO bound) while SQLite writes stay on
the calling thread.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app import config, db

CHUNK = 1024 * 1024


def _ffprobe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(CHUNK)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _parse_fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    if "/" in rate:
        num, den = rate.split("/", 1)
        try:
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f else None
        except ValueError:
            return None
    try:
        return round(float(rate), 3)
    except ValueError:
        return None


def thumb_path_for(segment_id: str) -> Path:
    safe = segment_id.replace(":", "_").replace("-", "__")
    return config.THUMBNAIL_DIR / f"{safe}.jpg"


def make_thumbnail(path: Path, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                    "-of", "csv=p=0", str(path)]
    dur = 0.0
    try:
        dur = float(subprocess.run(duration_cmd, capture_output=True, text=True,
                                   timeout=30).stdout.strip() or 0)
    except ValueError:
        dur = 0.0
    seek = max(0.0, dur / 2.0)
    cmd = [
        "ffmpeg", "-y", "-ss", f"{seek:.3f}", "-i", str(path),
        "-frames:v", "1", "-vf", "scale=480:-2", "-q:v", "3", str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError(f"thumbnail failed for {path.name}: {proc.stderr.strip()[:300]}")


def _probe_one(row: dict) -> dict:
    """Pure worker (no DB): probe + hash + thumbnail."""
    path = Path(row["file_path"])
    if not path.is_file():
        return {"segment_id": row["segment_id"], "ok": False, "error": "file missing"}
    try:
        probe = _ffprobe(path)
        video_stream = next(
            (s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None
        )
        if not video_stream:
            raise RuntimeError("no video stream")
        fmt = probe.get("format", {})
        duration_s = float(fmt.get("duration") or 0.0)
        duration_ms = int(round(duration_s * 1000)) if duration_s else None
        width = int(video_stream.get("width") or 0) or None
        height = int(video_stream.get("height") or 0) or None
        fps = _parse_fps(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))
        sha = _sha256(path)
        thumb = thumb_path_for(row["segment_id"])
        make_thumbnail(path, thumb)
        return {
            "segment_id": row["segment_id"],
            "ok": True,
            "file_size_bytes": path.stat().st_size,
            "sha256": sha,
            "duration_ms": duration_ms,
            "width": width,
            "height": height,
            "fps": fps,
            "codec": video_stream.get("codec_name"),
            "container": fmt.get("format_name"),
            "thumbnail_path": str(thumb),
            "expected_ms": row["end_ms"] - row["start_ms"],
        }
    except Exception as exc:  # noqa: BLE001 — record, continue (hardening)
        return {"segment_id": row["segment_id"], "ok": False, "error": str(exc)[:300]}


def run(force: bool = False, limit: int | None = None, workers: int = 6,
        progress=None) -> dict:
    db.init_db()
    conn = db.connect()
    try:
        sql = ("SELECT segment_id, file_path, start_ms, end_ms FROM segments "
               "WHERE file_path IS NOT NULL")
        if not force:
            sql += " AND status != 'ready'"
        sql += " ORDER BY segment_id"
        rows = [dict(r) for r in conn.execute(sql).fetchall()]
    finally:
        conn.close()
    if limit:
        rows = rows[:limit]
    if not rows:
        return {"probed_ok": 0, "failed": 0, "skipped": 0, "note": "nothing to probe"}

    ok = failed = 0
    failures: list[dict] = []
    duration_mismatch = 0
    total = len(rows)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_probe_one, r) for r in rows]
        done = 0
        for fut in as_completed(futures):
            res = fut.result()
            done += 1
            conn = db.connect()
            try:
                if res["ok"]:
                    expected = res.pop("expected_ms")
                    actual = res["duration_ms"]
                    if actual and expected and abs(actual - expected) > 1500:
                        duration_mismatch += 1
                    conn.execute(
                        """UPDATE segments SET file_size_bytes=?, sha256=?, duration_ms=?,
                           width=?, height=?, fps=?, codec=?, container=?, thumbnail_path=?,
                           status='ready' WHERE segment_id=?""",
                        (res["file_size_bytes"], res["sha256"], res["duration_ms"],
                         res["width"], res["height"], res["fps"], res["codec"],
                         res["container"], res["thumbnail_path"], res["segment_id"]),
                    )
                    ok += 1
                else:
                    failed += 1
                    failures.append({"segment": res["segment_id"], "error": res["error"]})
                    conn.execute("UPDATE segments SET status='probe_failed' WHERE segment_id=?",
                                 (res["segment_id"],))
                conn.commit()
            finally:
                conn.close()
            if progress and done % 25 == 0:
                progress(done / total, f"probed {done}/{total}")

    summary = {
        "probed_ok": ok,
        "failed": failed,
        "skipped": 0,
        "duration_mismatches_gt_1_5s": duration_mismatch,
        "failures": failures[:50],
    }
    conn = db.connect()
    try:
        db.set_meta(conn, "probe_report", json.dumps(summary, ensure_ascii=False))
        conn.commit()
    finally:
        conn.close()
    (config.DATA_DIR / "probe_report.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return summary
