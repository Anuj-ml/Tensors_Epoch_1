#!/usr/bin/env python
"""ReelMind worker — single Python worker for all pipeline stages.

Usage:
  python worker/pipeline.py ingest [--reset]
  python worker/pipeline.py probe [--force] [--limit N]
  python worker/pipeline.py language [--translate] [--limit N]
  python worker/pipeline.py embed [--limit N]
  python worker/pipeline.py index [--dry-run]
  python worker/pipeline.py previews [--limit N]
  python worker/pipeline.py explore [--refresh]
  python worker/pipeline.py all [--translate]
  python worker/pipeline.py report
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
import traceback
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "worker"))

from app import config, db  # noqa: E402

LOG_DIR = config.DATA_DIR / "logs"


def _setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("reelmind.worker")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(LOG_DIR / "worker.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger


logger = _setup_logging()


def _job_start(job_type: str) -> str:
    job_id = str(uuid.uuid4())
    conn = db.connect()
    try:
        conn.execute(
            "INSERT INTO jobs(job_id, job_type, status, progress) VALUES(?,?,?,?)",
            (job_id, job_type, "running", 0.0),
        )
        conn.commit()
    finally:
        conn.close()
    return job_id


def _job_progress(job_id: str, progress: float, message: str) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE jobs SET progress=?, detail=?, updated_at=datetime('now') WHERE job_id=?",
            (round(min(max(progress, 0.0), 1.0), 4), message, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _job_finish(job_id: str, status: str, detail: dict, error: str | None = None) -> None:
    conn = db.connect()
    try:
        conn.execute(
            "UPDATE jobs SET status=?, progress=COALESCE(?, progress), detail=?, error=?, "
            "updated_at=datetime('now') WHERE job_id=?",
            (status, 1.0 if status == "done" else None,
             json.dumps(detail, ensure_ascii=False)[:4000], error, job_id),
        )
        conn.commit()
    finally:
        conn.close()


def run_stage(name: str, fn, **kwargs) -> dict:
    job_id = _job_start(name)
    logger.info("stage %s started (job %s)", name, job_id)
    started = time.time()

    def progress(p: float, msg: str) -> None:
        _job_progress(job_id, p, msg)
        logger.info("[%s] %.0f%% %s", name, p * 100, msg)

    try:
        result = fn(progress=progress, **kwargs)
        elapsed = time.time() - started
        result = {**(result or {}), "elapsed_s": round(elapsed, 1)}
        _job_finish(job_id, "done", result)
        logger.info("stage %s finished in %.1fs: %s", name, elapsed,
                    json.dumps(result, ensure_ascii=False)[:600])
        return result
    except Exception as exc:  # noqa: BLE001 — record job failure, re-raise
        _job_finish(job_id, "failed", {}, error=str(exc)[:800])
        logger.error("stage %s failed: %s", name, exc)
        traceback.print_exc()
        raise


def cmd_report() -> dict:
    db.init_db()
    conn = db.connect()
    try:
        def one(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]

        report = {
            "videos_total": one("SELECT COUNT(*) FROM videos"),
            "videos_ready": one("SELECT COUNT(*) FROM videos WHERE status='ready'"),
            "segments_total": one("SELECT COUNT(*) FROM segments"),
            "segments_ready": one("SELECT COUNT(*) FROM segments WHERE status='ready'"),
            "segments_missing_file": one("SELECT COUNT(*) FROM segments WHERE status='missing_file'"),
            "annotations_total": one("SELECT COUNT(*) FROM annotations"),
            "annotations_active": one("SELECT COUNT(*) FROM annotations WHERE status='active'"),
            "annotations_english_ready": one(
                "SELECT COUNT(*) FROM annotations WHERE translation_status='NOT_NEEDED'"
            ),
            "annotations_translated_ok": one(
                "SELECT COUNT(*) FROM annotations WHERE translation_status='OK'"
            ),
            "annotations_pending_translation": one(
                "SELECT COUNT(*) FROM annotations WHERE translation_status='PENDING'"
            ),
            "annotations_translation_failed": one(
                "SELECT COUNT(*) FROM annotations WHERE translation_status LIKE 'FAILED%'"
            ),
            "annotations_indexed": one(
                "SELECT COUNT(*) FROM annotations WHERE vector_status='indexed'"
            ),
            "annotations_duplicate": one(
                "SELECT COUNT(*) FROM annotations WHERE is_duplicate=1"
            ),
            "searches": one("SELECT COUNT(*) FROM searches"),
            "story_clips": one("SELECT COUNT(*) FROM story_clips"),
            "explore_points": one("SELECT COUNT(*) FROM explore_points"),
        }
        for key in ("ingest_report", "probe_report", "language_report", "index_report"):
            raw = db.get_meta(conn, key)
            if raw:
                report[key] = json.loads(raw)
    finally:
        conn.close()

    try:
        from app.services import vectorstore, embeddings

        report["qdrant"] = vectorstore.collection_info()
        report["qdrant_reachable"] = True
        report["embedding"] = embeddings.model_info()
    except Exception as exc:  # noqa: BLE001
        report["qdrant_reachable"] = False
        report["qdrant_error"] = str(exc)[:200]

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reelmind-worker")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ingest = sub.add_parser("ingest", help="Phase 1: CSV + video matching → SQLite")
    p_ingest.add_argument("--reset", action="store_true")

    p_probe = sub.add_parser("probe", help="Phase 2: ffprobe/sha256/thumbnails")
    p_probe.add_argument("--force", action="store_true")
    p_probe.add_argument("--limit", type=int, default=None)

    p_lang = sub.add_parser("language", help="Phase 3: detection + optional local translation")
    p_lang.add_argument("--translate", action="store_true")
    p_lang.add_argument("--limit", type=int, default=None)
    p_lang.add_argument("--provider", default=None)
    p_lang.add_argument("--force", action="store_true", help="redo detection for all rows")

    p_embed = sub.add_parser("embed", help="Phase 4: local embedding shards")
    p_embed.add_argument("--limit", type=int, default=None)
    p_embed.add_argument("--shard-size", type=int, default=5000)

    p_index = sub.add_parser("index", help="Phase 5: upsert vectors into Qdrant")
    p_index.add_argument("--dry-run", action="store_true")
    p_index.add_argument("--batch", type=int, default=512)

    p_prev = sub.add_parser("previews", help="MP4 previews for browser playback")
    p_prev.add_argument("--limit", type=int, default=None)

    p_exp = sub.add_parser("explore", help="2D projection cache for Explore")
    p_exp.add_argument("--refresh", action="store_true")

    p_uni = sub.add_parser("universe", help="Visual Content Universe clustering cache")
    p_uni.add_argument("--refresh", action="store_true")

    p_all = sub.add_parser("all", help="ingest → probe → language → embed → index")
    p_all.add_argument("--translate", action="store_true")
    p_all.add_argument("--probe-limit", type=int, default=None)

    sub.add_parser("report", help="acceptance counters + system state")

    args = parser.parse_args(argv)
    db.init_db()

    if args.cmd == "ingest":
        from stages import ingest
        run_stage("ingest", ingest.run, reset=args.reset)
    elif args.cmd == "probe":
        from stages import probe
        run_stage("probe", probe.run, force=args.force, limit=args.limit)
    elif args.cmd == "language":
        from stages import language
        run_stage("language", language.run, translate=args.translate,
                  limit=args.limit, provider_name=args.provider, force=args.force)
    elif args.cmd == "embed":
        from stages import embed
        run_stage("embed", embed.run, limit=args.limit, shard_size=args.shard_size)
    elif args.cmd == "index":
        from stages import index
        run_stage("index", index.run, dry_run=args.dry_run, batch_size=args.batch)
    elif args.cmd == "previews":
        from stages import previews
        run_stage("previews", previews.run, limit=args.limit)
    elif args.cmd == "explore":
        from stages import explore
        run_stage("explore", explore.compute, refresh=args.refresh)
    elif args.cmd == "universe":
        from stages import universe
        run_stage("universe", universe.run, refresh=args.refresh)
    elif args.cmd == "all":
        from stages import embed, ingest, index, language, probe
        run_stage("ingest", ingest.run, reset=False)
        run_stage("probe", probe.run, force=False, limit=args.probe_limit)
        run_stage("language", language.run, translate=args.translate)
        run_stage("embed", embed.run, shard_size=5000)
        run_stage("index", index.run, batch_size=512)
    elif args.cmd == "report":
        cmd_report()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
