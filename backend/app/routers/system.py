"""Health, system state, and ingestion reports."""
from __future__ import annotations

import json

from fastapi import APIRouter

from .. import config, db
from ..services import embeddings, vectorstore
from ..services.language import provider_health

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health() -> dict:
    out: dict = {"status": "ok", "service": "reelmind"}
    try:
        out["qdrant"] = {"reachable": vectorstore.ping(),
                         **(vectorstore.collection_info() if vectorstore.ping() else {})}
    except Exception as exc:  # noqa: BLE001
        out["qdrant"] = {"reachable": False, "error": str(exc)[:200]}
    try:
        out["embedding"] = embeddings.model_info()
    except Exception as exc:  # noqa: BLE001
        out["embedding"] = {"error": str(exc)[:200]}
    out["translation_provider"] = provider_health()
    try:
        from ..services import visualstore

        vs = visualstore.status()
        out["image_search"] = {
            "status": "implemented",
            "provider": vs.get("provider", "clip"),
            "model": vs.get("model"),
            "collection": vs.get("collection"),
            "points": vs.get("points", 0),
            "indexed": bool(vs.get("indexed")),
        }
        if vs.get("reachable") and not vs.get("indexed"):
            out["image_search"]["hint"] = "run: python worker/pipeline.py visual"
    except Exception as exc:  # noqa: BLE001
        out["image_search"] = {"status": "implemented", "error": str(exc)[:200]}
    try:
        import shutil

        out["ffmpeg"] = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
    except Exception:  # noqa: BLE001
        out["ffmpeg"] = False
    if out["qdrant"].get("reachable") is not True:
        out["status"] = "degraded"
    return out


@router.get("/stats")
def stats() -> dict:
    db.init_db()
    conn = db.connect()
    try:
        def one(sql: str) -> int:
            return conn.execute(sql).fetchone()[0]

        out = {
            "videos": {"total": one("SELECT COUNT(*) FROM videos"),
                       "ready": one("SELECT COUNT(*) FROM videos WHERE status='ready'"),
                       "missing_files": one("SELECT COUNT(*) FROM videos WHERE status='missing_files'")},
            "segments": {"total": one("SELECT COUNT(*) FROM segments"),
                         "ready": one("SELECT COUNT(*) FROM segments WHERE status='ready'"),
                         "missing_file": one("SELECT COUNT(*) FROM segments WHERE status='missing_file'"),
                         "probe_failed": one("SELECT COUNT(*) FROM segments WHERE status='probe_failed'")},
            "annotations": {
                "total": one("SELECT COUNT(*) FROM annotations"),
                "english_ready": one("SELECT COUNT(*) FROM annotations WHERE translation_status='NOT_NEEDED'"),
                "translated_ok": one("SELECT COUNT(*) FROM annotations WHERE translation_status='OK'"),
                "pending_translation": one("SELECT COUNT(*) FROM annotations WHERE translation_status='PENDING'"),
                "translation_failed": one("SELECT COUNT(*) FROM annotations WHERE translation_status LIKE 'FAILED%'"),
                "duplicates": one("SELECT COUNT(*) FROM annotations WHERE is_duplicate=1"),
                "indexed": one("SELECT COUNT(*) FROM annotations WHERE vector_status='indexed'"),
            },
            "searches": one("SELECT COUNT(*) FROM searches"),
            "story_clips": one("SELECT COUNT(*) FROM story_clips"),
            "explore_points": one("SELECT COUNT(*) FROM explore_points"),
        }
        reports = {}
        for key in ("ingest_report", "probe_report", "language_report", "index_report"):
            raw = db.get_meta(conn, key)
            if raw:
                reports[key] = json.loads(raw)
        out["reports"] = reports
        lang = conn.execute(
            "SELECT detected_language, COUNT(*) c FROM annotations "
            "WHERE status='active' GROUP BY 1 ORDER BY c DESC LIMIT 12"
        ).fetchall()
        out["languages"] = {r["detected_language"]: r["c"] for r in lang}
    finally:
        conn.close()
    try:
        out["qdrant"] = vectorstore.collection_info()
        out["embedding"] = embeddings.model_info()
    except Exception as exc:  # noqa: BLE001
        out["vector_store_error"] = str(exc)[:200]
    return out


@router.get("/metrics")
def metrics() -> dict:
    """Retrieval/product metrics (ARCHITECTURE §16)."""
    from ..services.observability import metrics as snapshot

    return snapshot()
