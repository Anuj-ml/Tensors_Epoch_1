"""Phase 12 support — 2D projection of indexed segment vectors for Explore.

Uses the vectors already stored in local Qdrant (no re-embedding) and
TruncatedSVD (linear PCA-style reduction) for deterministic, dependency-light
2D coordinates. Coordinates are cached in SQLite (explore_points).
"""
from __future__ import annotations

import json
import time
from collections import defaultdict

import numpy as np

from app import config, db
from app.services import vectorstore


def compute(refresh: bool = False, progress=None) -> dict:
    db.init_db()
    conn = db.connect()
    try:
        existing = conn.execute("SELECT COUNT(*) c FROM explore_points").fetchone()["c"]
        if existing and not refresh:
            return {"points": existing, "recomputed": False}

        groups: dict[str, list[np.ndarray]] = defaultdict(list)
        total = vectorstore.count_points()
        if total == 0:
            raise RuntimeError("Qdrant collection is empty — run the 'index' stage first")
        seen = 0
        for rec in vectorstore.iter_all_vectors(batch=512):
            seg = (rec.payload or {}).get("segment_id")
            vec = rec.vector
            if seg and vec:
                groups[seg].append(np.asarray(vec, dtype=np.float32))
            seen += 1
            if progress and seen % 5000 == 0:
                progress(seen / total * 0.6, f"loaded {seen}/{total} vectors")

        if not groups:
            raise RuntimeError("no segment vectors found in Qdrant")

        seg_ids = sorted(groups.keys())
        matrix = np.vstack([
            np.mean(np.vstack(groups[s]), axis=0) for s in seg_ids
        ]).astype(np.float32)

        if progress:
            progress(0.7, f"reducing {len(seg_ids)} segments to 2D")
        from sklearn.decomposition import TruncatedSVD

        reducer = TruncatedSVD(n_components=2, random_state=42)
        coords = reducer.fit_transform(matrix)

        conn.execute("DELETE FROM explore_points")
        conn.executemany(
            "INSERT INTO explore_points(segment_id, x, y, model) VALUES(?,?,?,?)",
            [
                (seg_id, float(x), float(y), "TruncatedSVD2-on-mean-annotation-vectors")
                for seg_id, (x, y) in zip(seg_ids, coords)
            ],
        )
        db.set_meta(conn, "explore_report", json.dumps({
            "points": len(seg_ids),
            "explained_variance": [float(v) for v in reducer.explained_variance_ratio_],
        }))
        conn.commit()
        return {
            "points": len(seg_ids),
            "recomputed": True,
            "explained_variance": [round(float(v), 4) for v in reducer.explained_variance_ratio_],
            "model": "TruncatedSVD2",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
    finally:
        conn.close()
