"""Observability — search/job metrics and structured logs (ARCHITECTURE §16).

Logs IDs, models, counts and scores only — never raw media or private content.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from .. import config, db

logger = logging.getLogger("reelmind.metrics")


def log_search(
    search_id: str,
    query_type: str,
    model: str,
    candidate_count: int,
    top_scores: list[float],
    filters: dict | None,
    latency_ms: float,
    result_count: int | None = None,
) -> None:
    """One structured line per search (§16 Log requirements)."""
    line = (
        f"search_id={search_id} query_type={query_type} model={model} "
        f"candidates={candidate_count} results={result_count} "
        f"top_scores={[round(s, 4) for s in top_scores[:5]]} "
        f"filters={json.dumps(filters or {}, ensure_ascii=False, sort_keys=True)} "
        f"latency_ms={latency_ms:.1f}"
    )
    logger.info(line)


def log_event(event_type: str, payload: dict | None = None) -> None:
    """Persist a history event (§10 event names). Fire-and-forget safe."""
    try:
        conn = db.connect()
        try:
            conn.execute(
                "INSERT INTO history_events(event_type, payload) VALUES(?,?)",
                (event_type, json.dumps(payload or {}, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — metrics must never break the request
        logger.warning("event log failed (%s): %s", event_type, exc)


def metrics() -> dict[str, Any]:
    """Aggregated product metrics (§16 Core metrics)."""
    conn = db.connect()
    try:
        def one(sql: str, args: tuple = ()) -> int:
            row = conn.execute(sql, args).fetchone()
            return int(row[0]) if row else 0

        by_type = conn.execute(
            "SELECT query_type, COUNT(*) c, AVG(result_count) avg_results "
            "FROM searches GROUP BY query_type"
        ).fetchall()
        events = conn.execute(
            "SELECT event_type, COUNT(*) c FROM history_events GROUP BY event_type"
        ).fetchall()
        jobs = conn.execute(
            "SELECT job_type, status, COUNT(*) c, "
            "AVG((julianday(updated_at) - julianday(created_at)) * 86400.0) avg_s "
            "FROM jobs GROUP BY job_type, status"
        ).fetchall()
        out: dict[str, Any] = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "searches": {
                "total": one("SELECT COUNT(*) FROM searches"),
                "saved": one("SELECT COUNT(*) FROM searches WHERE is_saved=1"),
                "by_type": {
                    r["query_type"]: {
                        "count": r["c"],
                        "avg_results": round(float(r["avg_results"] or 0), 2),
                    }
                    for r in by_type
                },
            },
            "events": {r["event_type"]: r["c"] for r in events},
            "story": {
                "clips": one("SELECT COUNT(*) FROM story_clips"),
                "projects": one("SELECT COUNT(*) FROM story_projects"),
                "adds_last_30d": one(
                    "SELECT COUNT(*) FROM history_events "
                    "WHERE event_type='scene.added_to_story' "
                    "AND created_at >= datetime('now','-30 days')"
                ),
                "find_similar_last_30d": one(
                    "SELECT COUNT(*) FROM history_events "
                    "WHERE event_type='scene.find_similar' "
                    "AND created_at >= datetime('now','-30 days')"
                ),
            },
            "jobs": [
                {"job_type": r["job_type"], "status": r["status"], "count": r["c"],
                 "avg_seconds": round(float(r["avg_s"] or 0), 2)}
                for r in jobs
            ],
            "library": {
                "segments_ready": one(
                    "SELECT COUNT(*) FROM segments WHERE status='ready'"
                ),
                "indexed_annotations": one(
                    "SELECT COUNT(*) FROM annotations WHERE vector_status='indexed'"
                ),
            },
        }
    finally:
        conn.close()

    try:
        from . import vectorstore, visualstore

        out["retrieval"] = {
            "text_collection": vectorstore.collection_info(),
            "vision_collection": visualstore.status(),
        }
    except Exception as exc:  # noqa: BLE001
        out["retrieval_error"] = str(exc)[:200]

    # Retrieval quality from the evaluation run (§17), when available.
    eval_path = config.DATA_DIR / "eval_report.json"
    if eval_path.exists():
        try:
            out["retrieval_quality"] = json.loads(eval_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return out
