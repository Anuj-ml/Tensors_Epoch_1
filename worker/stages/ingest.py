"""Phase 1 — dataset discovery & ingestion.

Reads the MSR annotation CSV (read-only) and the video directory, matches
videos to annotations with the exact identity `video_id + start + end`,
and writes everything into SQLite. Original files are never modified.
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from app import config, db
from app.ids import annotation_id, file_slug, segment_id

FILENAME_RE = re.compile(r"^(?P<vid>.+)_(?P<start>\d+)_(?P<end>\d+)\.avi$", re.IGNORECASE)


def scan_video_dir(video_dir: Path) -> tuple[dict[tuple[str, int, int], Path], dict]:
    """Parse {VideoID}_{Start}_{End}.avi → exact segment identity."""
    segments: dict[tuple[str, int, int], Path] = {}
    bad: list[str] = []
    ids: set[str] = set()
    for path in sorted(video_dir.iterdir()):
        if not path.is_file():
            continue
        m = FILENAME_RE.match(path.name)
        if not m:
            bad.append(path.name)
            continue
        vid = m.group("vid")
        start, end = int(m.group("start")), int(m.group("end"))
        segments[(vid, start, end)] = path
        ids.add(vid)
    stats = {
        "files_total": len(segments) + len(bad),
        "files_parsed": len(segments),
        "files_unparsed": len(bad),
        "unparsed_names": bad[:20],
        "unique_video_ids_on_disk": len(ids),
    }
    return segments, stats


def run(reset: bool = False, progress=None) -> dict:
    db.init_db()
    video_dir = config.VIDEO_DIR
    csv_path = config.DATASET_CSV
    if not video_dir.is_dir():
        raise FileNotFoundError(f"video directory not found: {video_dir}")
    if not csv_path.is_file():
        raise FileNotFoundError(f"dataset csv not found: {csv_path}")

    file_segments, file_stats = scan_video_dir(video_dir)

    invalid_rows: list[dict] = []
    rows_total = 0
    lang_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    seen_full: Counter[tuple] = Counter()
    seen_seg_desc: set[tuple[str, str]] = set()
    seg_ann_number: dict[str, int] = defaultdict(int)
    ann_rows: list[tuple] = []
    seg_order: list[tuple[str, int, int]] = []
    seg_seen: set[tuple[str, int, int]] = set()
    csv_video_ids: set[str] = set()
    dup_full = 0
    dup_seg_desc = 0
    empty_desc = 0
    bad_range = 0

    with open(csv_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        required = {"VideoID", "Start", "End", "Description"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV missing expected columns: {missing}")
        for row in reader:
            rows_total += 1
            if progress and rows_total % 20000 == 0:
                progress(rows_total / 122665 * 0.6, f"read {rows_total} csv rows")
            vid = (row.get("VideoID") or "").strip()
            desc = (row.get("Description") or "").strip()
            try:
                start = int(str(row.get("Start")).strip())
                end = int(str(row.get("End")).strip())
            except (TypeError, ValueError):
                bad_range += 1
                invalid_rows.append({"csv_row": rows_total, "reason": "bad_start_end",
                                     "VideoID": vid, "Start": row.get("Start"), "End": row.get("End")})
                continue
            if not vid:
                invalid_rows.append({"csv_row": rows_total, "reason": "missing_video_id"})
                continue
            if start >= end:
                bad_range += 1
                invalid_rows.append({"csv_row": rows_total, "reason": "start_ge_end",
                                     "VideoID": vid, "Start": start, "End": end})
                continue
            if not desc or desc.lower() in ("n/a", "na", "null", "none", "nan", "-"):
                empty_desc += 1
                invalid_rows.append({"csv_row": rows_total, "reason": "empty_description",
                                     "VideoID": vid, "raw": desc})
                # text cannot be embedded — recorded as an invalid record, not stored
                continue

            csv_video_ids.add(vid)
            lang = (row.get("Language") or "").strip()
            source = (row.get("Source") or "").strip()
            lang_counter[lang or "<blank>"] += 1
            source_counter[source or "<blank>"] += 1

            seg_id = segment_id(vid, start, end)
            if (vid, start, end) not in seg_seen:
                seg_seen.add((vid, start, end))
                seg_order.append((vid, start, end))

            full_key = (vid, start, end, desc, lang, source,
                        row.get("WorkerID"), row.get("AnnotationTime"))
            seen_full[full_key] += 1
            if seen_full[full_key] > 1:
                dup_full += 1
            seg_desc_key = (seg_id, desc)
            is_dup = 1 if seg_desc_key in seen_seg_desc else 0
            if is_dup:
                dup_seg_desc += 1
            else:
                seen_seg_desc.add(seg_desc_key)

            seg_ann_number[seg_id] += 1
            csv_row = rows_total  # 1-based data row ordinal (unique)
            try:
                worker_id = int(row.get("WorkerID")) if str(row.get("WorkerID") or "").strip() else None
            except ValueError:
                worker_id = None
            try:
                ann_time = int(row.get("AnnotationTime")) if str(row.get("AnnotationTime") or "").strip() else None
            except ValueError:
                ann_time = None

            ann_rows.append((
                annotation_id(csv_row), seg_id, seg_ann_number[seg_id], csv_row, desc,
                lang or None, None, None, "PENDING", "active",
                source or None, worker_id, ann_time, is_dup, "pending", None,
            ))

    total_annotations = len(ann_rows)
    english_declared = sum(c for l, c in lang_counter.items() if l.lower() == "english")
    non_english_declared = total_annotations - english_declared

    matched_ann = 0
    unmatched_ann = 0
    ann_with_file: set[str] = set()
    for row in ann_rows:
        seg = row[1]
        if _segment_key(seg) in file_segments:
            matched_ann += 1
            ann_with_file.add(seg)
        else:
            unmatched_ann += 1

    csv_seg_keys = set(seg_order)
    file_only = [k for k in file_segments if k not in csv_seg_keys]
    missing_file_keys = [k for k in seg_order if k not in file_segments]
    csv_ids_without_file = {vid for vid, _, _ in missing_file_keys}
    file_ids = {vid for vid, _, _ in file_segments}

    # ---- write to SQLite (upsert; source CSV untouched) ----
    conn = db.connect()
    try:
        conn.execute("BEGIN")
        if reset:
            for table in ("search_results", "searches", "history_events", "script_beats",
                          "script_analyses", "story_clips", "story_projects", "explore_points",
                          "annotations", "segments", "videos"):
                conn.execute(f"DELETE FROM {table}")

        all_video_ids = csv_video_ids | file_ids
        conn.executemany(
            """INSERT INTO videos(video_id, status, file_count) VALUES(?,?,?)
               ON CONFLICT(video_id) DO UPDATE SET status=excluded.status,
                                                   file_count=excluded.file_count""",
            [
                (
                    vid,
                    "ready" if vid in file_ids else "missing_files",
                    sum(1 for v, _, _ in file_segments if v == vid),
                )
                for vid in sorted(all_video_ids)
            ],
        )

        seg_rows = []
        for vid, start, end in seg_order:
            key = (vid, start, end)
            path = file_segments.get(key)
            seg_rows.append((
                segment_id(vid, start, end), vid, start * 1000, end * 1000,
                config.SEGMENTATION_VERSION,
                str(path) if path else None,
                path.name if path else None,
                None, None, None, None, None, None, None, None, None, None,
                "pending" if path else "missing_file",
            ))
        for vid, start, end in file_only:
            path = file_segments[(vid, start, end)]
            seg_rows.append((
                segment_id(vid, start, end), vid, start * 1000, end * 1000,
                config.SEGMENTATION_VERSION, str(path), path.name,
                None, None, None, None, None, None, None, None, None, None,
                "pending",
            ))
        conn.executemany(
            """INSERT INTO segments(segment_id, video_id, start_ms, end_ms, segmentation_version,
                 file_path, file_name, file_size_bytes, sha256, duration_ms, width, height,
                 fps, codec, container, thumbnail_path, preview_path, status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(segment_id) DO UPDATE SET
                 video_id=excluded.video_id, start_ms=excluded.start_ms, end_ms=excluded.end_ms,
                 file_path=COALESCE(excluded.file_path, segments.file_path),
                 file_name=COALESCE(excluded.file_name, segments.file_name),
                 status=excluded.status""",
            seg_rows,
        )

        conn.executemany(
            """INSERT INTO annotations(annotation_id, segment_id, annotation_number, csv_row,
                 description_original, declared_language, detected_language, description_english,
                 translation_status, status, source, worker_id, annotation_time, is_duplicate,
                 vector_status, embedding_model)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(csv_row) DO UPDATE SET
                 segment_id=excluded.segment_id, annotation_number=excluded.annotation_number,
                 description_original=excluded.description_original,
                 declared_language=excluded.declared_language,
                 is_duplicate=excluded.is_duplicate""",
            ann_rows,
        )

        # video file counts for ids that only exist on disk
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    report = {
        "csv_rows_read": rows_total,
        "total_videos_on_disk": file_stats["files_parsed"],
        "total_video_ids": len(all_video_ids),
        "total_csv_video_ids": len(csv_video_ids),
        "total_segments": len(seg_order),
        "segments_with_file": len(seg_order) - len(missing_file_keys),
        "segments_missing_file": len(missing_file_keys),
        "file_only_segments": len(file_only),
        "total_annotations": total_annotations,
        "english_annotations_declared": english_declared,
        "non_english_annotations_declared": non_english_declared,
        "matched_annotations": matched_ann,
        "unmatched_annotations": unmatched_ann,
        "duplicate_full_rows": dup_full,
        "duplicate_segment_description_rows": dup_seg_desc,
        "invalid_records": len(invalid_rows),
        "invalid_reasons": dict(Counter(r["reason"] for r in invalid_rows)),
        "invalid_samples": invalid_rows[:20],
        "declared_languages": dict(lang_counter.most_common()),
        "sources": dict(source_counter),
        "video_ids_without_files": len(csv_ids_without_file),
        "filename_parse_failures": file_stats["files_unparsed"],
        "unique_segments_indexed_ready": sum(
            1 for r in seg_rows if r[-1] == "ready"
        ),
    }

    db.set_meta(conn, "ingest_report", json.dumps(report, ensure_ascii=False))
    conn.commit()
    conn.close()

    out = config.DATA_DIR / "ingest_report.json"
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return report


def _segment_key(seg_id: str) -> tuple[str, int, int]:
    vid, _, rng = seg_id.rpartition(":")
    s, e = rng.split("-")
    return vid, int(s), int(e)


def slug_for(seg_id: str) -> str:
    vid, start, end = _segment_key(seg_id)
    return file_slug(vid, start, end)
