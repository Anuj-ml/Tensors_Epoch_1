"""Vision embedding stage — representative frames → CLIP → Qdrant (§5/§6).

One pooled CLIP vector per extracted keyframe, grouped by scene at query
time. Frames live under media/frames/ (derived media); source clips are
never modified. Vectors go into the CLIP collection only — the text path
never writes there (and vice versa).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app import config, db
from app.services import clip, visualstore


def frame_path_for(segment_id: str, frame_index: int) -> Path:
    safe = segment_id.replace(":", "_").replace("-", "__")
    return config.FRAME_DIR / f"{safe}_k{frame_index}.jpg"


def extract_frame(file_path: Path, t: float, out: Path) -> None:
    """Single keyframe at t seconds (fast seek, scaled for CLIP, §5)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and out.stat().st_size > 0:
        return
    cmd = [
        "ffmpeg", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(file_path),
        "-frames:v", "1", "-vf", "scale='min(512,iw)':-2",
        "-q:v", "3", str(out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"frame extraction failed: {proc.stderr.strip()[-300:]}")


def frame_offsets(duration_ms: int, count: int) -> list[float]:
    """Even interior offsets (e.g. 2 frames → 1/3 and 2/3 of the clip)."""
    count = max(1, int(count))
    duration_s = max(0.1, (duration_ms or 1000) / 1000.0)
    return [duration_s * (i + 1) / (count + 1) for i in range(count)]


def _record_embedding_meta(conn, segment_ids: set[str], model: str,
                           version: str, dim: int) -> None:
    conn.executemany(
        """INSERT OR IGNORE INTO embedding_meta(segment_id, model_name, model_version,
                                                 modality, vector_version, dim)
           VALUES(?,?,?,'image',?,?)""",
        [(s, model, version, config.VECTOR_VERSION, dim) for s in segment_ids],
    )
    conn.commit()


def run(limit: int | None = None, refresh: bool = False,
        frames_per_segment: int | None = None, progress=None) -> dict:
    db.init_db()
    info = clip.model_info()
    dim = info["dim"]
    visualstore.ensure_collection(dim)

    conn = db.connect()
    try:
        rows = conn.execute(
            """SELECT s.segment_id, s.video_id, s.file_path, s.duration_ms,
                      s.start_ms, s.end_ms, s.thumbnail_path, s.file_name
               FROM segments s JOIN videos v ON v.video_id = s.video_id
               WHERE s.status = 'ready' AND s.file_path IS NOT NULL
               ORDER BY s.segment_id"""
        ).fetchall()
    finally:
        conn.close()
    if limit:
        rows = rows[:limit]

    fps = max(1, int(frames_per_segment or config.FRAMES_PER_SEGMENT))
    total = len(rows)
    skipped = failed = 0
    failures: list[dict] = []
    indexed_segments: set[str] = set()

    # ---- pass 1: extract (or reuse) keyframes ----------------------------
    queue: list[tuple[str, dict, Path]] = []  # (segment_id, payload, frame_path)
    for i, row in enumerate(rows):
        seg = row["segment_id"]
        if not refresh and visualstore.has_segment(seg):
            skipped += 1
            if progress and i % 25 == 0:
                progress((i + 1) / max(total, 1), f"frames {i + 1}/{total}")
            continue
        try:
            if refresh:
                visualstore.delete_segment_frames(seg)
            offsets = frame_offsets(row["duration_ms"] or 1000, fps)
            for idx, t in enumerate(offsets):
                out = frame_path_for(seg, idx)
                if refresh:
                    out.unlink(missing_ok=True)
                extract_frame(Path(row["file_path"]), t, out)
                queue.append((seg, {
                    "segment_id": seg,
                    "video_id": row["video_id"],
                    "start_ms": row["start_ms"],
                    "end_ms": row["end_ms"],
                    "duration_ms": row["duration_ms"],
                    "frame_index": idx,
                    "frame_offset_ms": int(t * 1000),
                    "frame_path": str(out),
                    "video_filename": row["file_name"],
                    "thumbnail_path": row["thumbnail_path"],
                    "modality": "image",
                    "embedding_model": info["model"],
                    "dim": dim,
                    "vector_version": config.VECTOR_VERSION,
                }, out))
        except Exception as exc:  # noqa: BLE001 — one bad clip must not stop the stage
            failed += 1
            if len(failures) < 50:
                failures.append({"segment": seg, "error": str(exc)[:300]})
        if progress and i % 25 == 0:
            progress((i + 1) / max(total, 1), f"frames {i + 1}/{total}")

    # ---- pass 2: batch CLIP encode → upsert ------------------------------
    from app.services.visualstore import frame_point_id

    batch_ids: list[str] = []
    batch_vecs: list = []
    batch_pl: list[dict] = []
    frames_indexed = 0

    def flush() -> None:
        if not batch_ids:
            return
        visualstore.upsert_frames(batch_ids, batch_vecs, batch_pl)
        batch_ids.clear()
        batch_vecs.clear()
        batch_pl.clear()

    for n, (seg, payload, path) in enumerate(queue):
        vectors, kept = clip.encode_image_paths([path])
        if not kept:
            continue
        batch_ids.append(frame_point_id(seg, payload["frame_index"]))
        batch_vecs.append(vectors[0].tolist())
        batch_pl.append(payload)
        indexed_segments.add(seg)
        frames_indexed += 1
        if len(batch_ids) >= max(config.CLIP_BATCH_SIZE * 2, 128):
            flush()
        if progress and n % 64 == 0 and queue:
            progress(0.9 * (n + 1) / len(queue), f"encoded {n + 1}/{len(queue)} frames")
    flush()

    conn = db.connect()
    try:
        if indexed_segments:
            _record_embedding_meta(conn, indexed_segments, info["model"],
                                   info.get("version", "local"), dim)
        visual_points = visualstore.count_points()
        stats = {
            "collection": visualstore.collection_name(),
            "dim": dim,
            "model": info["model"],
            "segments_total": total,
            "segments_skipped": skipped,
            "segments_indexed": len(indexed_segments),
            "frames_indexed": frames_indexed,
            "failed": failed,
            "failures": failures,
            "visual_points_after": visual_points,
            "refresh": bool(refresh),
        }
        db.set_meta(conn, "visual_report", json.dumps(stats, ensure_ascii=False))
        conn.commit()
    finally:
        conn.close()
    (config.DATA_DIR / "visual_report.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if progress:
        progress(1.0, f"visual index: {visual_points} frame vectors")
    return stats
