"""Phase 5 — Qdrant indexing (local Qdrant at localhost:6333, no API key).

Reads embedding shards produced by Phase 4 and upserts them into a
collection sized from the model's real dimension (384 for MiniLM). The
pre-existing 512-dim legacy collection is inspected and left untouched.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from app import config, db
from app.ids import point_id
from app.services import embeddings, vectorstore

PAYLOAD_SELECT = """
SELECT a.annotation_id, a.segment_id, a.description_original, a.description_english,
       a.detected_language, a.declared_language, s.video_id, s.start_ms, s.end_ms,
       s.duration_ms, s.file_name, s.thumbnail_path, v.status AS video_status
FROM annotations a
JOIN segments s ON s.segment_id = a.segment_id
JOIN videos v ON v.video_id = s.video_id
WHERE a.annotation_id IN ({placeholders})
"""


def _payloads_for(conn, ids: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 400):
        chunk = ids[i:i + 400]
        ph = ",".join("?" * len(chunk))
        for row in conn.execute(PAYLOAD_SELECT.format(placeholders=ph), chunk):
            out[row["annotation_id"]] = {
                "segment_id": row["segment_id"],
                "video_id": row["video_id"],
                "start_ms": row["start_ms"],
                "end_ms": row["end_ms"],
                "duration_ms": row["duration_ms"],
                "annotation_id": row["annotation_id"],
                "description_original": row["description_original"],
                "description_english": row["description_english"],
                "detected_language": row["detected_language"],
                "declared_language": row["declared_language"],
                "video_filename": row["file_name"],
                "thumbnail_path": row["thumbnail_path"],
                "video_status": row["video_status"],
            }
    return out


def _manifest_dir() -> Path:
    from .embed import _slug

    return config.DATA_DIR / "embeddings" / _slug(config.EMBEDDING_MODEL)


def run(batch_size: int = 512, dry_run: bool = False, progress=None) -> dict:
    db.init_db()
    info = embeddings.model_info()
    dim = info["dim"]

    manifest_path = _manifest_dir() / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"no embedding manifest at {manifest_path}; run the 'embed' stage first"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("dim") != dim:
        raise RuntimeError(
            f"manifest dim {manifest.get('dim')} != model dim {dim}; re-run 'embed'"
        )

    coll = vectorstore.collection_info() if _collection_exists() else None
    if coll and coll["dim"] != dim:
        raise RuntimeError(
            f"collection {coll['name']} has dim {coll['dim']} but model produces {dim}. "
            "Choose a new REELMIND_QDRANT_COLLECTION value — existing collections are "
            "never deleted or recreated."
        )
    if dry_run:
        return {"would_index": manifest.get("total_vectors", 0), "dim": dim,
                "collection": config.QDRANT_COLLECTION, "dry_run": True}

    vectorstore.ensure_collection(dim)
    before = vectorstore.count_points()

    conn = db.connect()
    total_upserted = 0
    try:
        shards = manifest["shards"]
        for si, shard in enumerate(shards):
            npz_path = _manifest_dir() / shard["file"]
            if not npz_path.exists():
                raise FileNotFoundError(f"missing shard {npz_path}")
            data = np.load(npz_path)
            ids = [str(x) for x in data["ids"].tolist()]
            vectors = data["vectors"]
            if vectors.shape[1] != dim:
                raise RuntimeError(f"shard {shard['file']} has wrong dim {vectors.shape[1]}")

            payloads = _payloads_for(conn, ids)
            for i in range(0, len(ids), batch_size):
                b_ids = ids[i:i + batch_size]
                b_vecs = vectors[i:i + batch_size]
                b_pl = []
                skip = []
                for pid, vec in zip(b_ids, b_vecs):
                    pl = payloads.get(pid)
                    if pl is None:
                        skip.append(pid)
                        continue
                    pl["point_id"] = pid
                    pl["embedding_model"] = manifest["model"]
                    b_pl.append(pl)
                if not b_ids:
                    continue
                point_ids = [point_id(p["segment_id"], p["annotation_id"]) for p in b_pl]
                vectors_list = b_vecs[: len(b_pl)].tolist()
                vectorstore.upsert(point_ids, vectors_list, b_pl)
                if skip:
                    conn.executemany(
                        "UPDATE annotations SET vector_status='orphaned' "
                        "WHERE annotation_id=?",
                        [(s,) for s in skip],
                    )
                conn.executemany(
                    "UPDATE annotations SET vector_status='indexed', embedding_model=? "
                    "WHERE annotation_id=?",
                    [(manifest["model"], p["annotation_id"]) for p in b_pl],
                )
                conn.commit()
                total_upserted += len(b_pl)
                if progress:
                    done_shard = si + (i + batch_size) / len(ids)
                    progress(done_shard / len(shards),
                             f"indexed {total_upserted} points ({si + 1}/{len(shards)} shards)")

        after = vectorstore.count_points()
        stats = {
            "collection": config.QDRANT_COLLECTION,
            "dim": dim,
            "points_before": before,
            "points_after": after,
            "upserted": total_upserted,
            "model": manifest["model"],
            "collection_info": vectorstore.collection_info(),
            "legacy_collections_untouched": list(config.LEGACY_COLLECTIONS),
        }
        db.set_meta(conn, "index_report", json.dumps(stats, ensure_ascii=False))
        conn.commit()
        (config.DATA_DIR / "index_report.json").write_text(
            json.dumps(stats, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return stats
    finally:
        conn.close()


def _collection_exists() -> bool:
    try:
        client = vectorstore.get_client()
        names = {c.name for c in client.get_collections().collections}
        return config.QDRANT_COLLECTION in names
    except Exception:
        return False
