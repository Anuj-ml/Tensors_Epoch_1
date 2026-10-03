"""Phase 4 — embedding pipeline (local model, no embedding API).

Pipeline: existing description → English normalization → embedding model → vector.
There is NO video-caption generation stage: CSV descriptions are the only text.

Vectors are written as shards under data/embeddings/<model-slug>/ so the
indexing phase can run (and resume) independently.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np

from app import config, db
from app.services import embeddings

SELECT_SQL = """
SELECT a.annotation_id, a.description_english, a.description_original
FROM annotations a
JOIN segments s ON s.segment_id = a.segment_id
WHERE a.status = 'active'
  AND a.is_duplicate = 0
  AND a.vector_status = 'pending'
  AND a.translation_status IN ('NOT_NEEDED', 'OK')
  AND a.description_english IS NOT NULL
  AND a.description_english != ''
  AND s.status = 'ready'
ORDER BY a.csv_row
"""


def _slug(model_name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model_name).strip("_")


def run(shard_size: int = 5000, limit: int | None = None, progress=None) -> dict:
    db.init_db()
    info = embeddings.model_info()
    model_name, dim = info["model"], info["dim"]

    conn = db.connect()
    try:
        rows = conn.execute(SELECT_SQL).fetchall()
    finally:
        conn.close()
    if limit:
        rows = rows[:limit]
    if not rows:
        return {"pending": 0, "embedded": 0, "model": model_name, "dim": dim}

    out_dir = config.DATA_DIR / "embeddings" / _slug(model_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {"model": model_name, "dim": dim, "shards": [], "total_vectors": 0}
    if manifest["dim"] != dim:
        raise RuntimeError(
            f"existing manifest was built with dim={manifest['dim']} but model "
            f"{model_name} produces {dim}; refusing to mix vector sizes"
        )

    total = len(rows)
    done = 0
    shard_idx = len(manifest["shards"])
    started = time.time()
    for offset in range(0, total, shard_size):
        chunk = rows[offset:offset + shard_size]
        ids = [r["annotation_id"] for r in chunk]
        texts = [
            (r["description_english"] or "").strip() or (r["description_original"] or "").strip()
            for r in chunk
        ]
        vectors = embeddings.embed_texts(texts, model_name=model_name)
        if vectors.shape[1] != dim:
            raise RuntimeError(f"model returned {vectors.shape[1]}-dim vectors, expected {dim}")

        npy_path = out_dir / f"shard_{shard_idx:05d}.npz"
        np.savez_compressed(
            npy_path,
            ids=np.array(ids, dtype="U64"),
            vectors=vectors.astype(np.float32),
        )
        manifest["shards"].append({"file": npy_path.name, "count": len(ids)})
        manifest["total_vectors"] += len(ids)
        manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        conn = db.connect()
        try:
            conn.executemany(
                "UPDATE annotations SET vector_status='embedded', embedding_model=? "
                "WHERE annotation_id=?",
                [(model_name, i) for i in ids],
            )
            conn.commit()
        finally:
            conn.close()

        done += len(chunk)
        shard_idx += 1
        if progress:
            elapsed = time.time() - started
            rate = done / max(elapsed, 0.001)
            progress(done / total, f"embedded {done}/{total} ({rate:.0f} txt/s)")

    return {
        "pending": total,
        "embedded": done,
        "model": model_name,
        "dim": dim,
        "manifest": str(manifest_path),
        "total_vectors_in_manifest": manifest["total_vectors"],
        "elapsed_s": round(time.time() - started, 1),
    }
