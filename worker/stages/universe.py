"""Visual Content Universe — hierarchical semantic clustering of the library.

Derives galaxies / subclusters from the real MiniLM segment embeddings
(no hardcoded categories):

  1. Load 70,051 annotation vectors from the local embedding shards,
     average them per segment (1,970 segments) and L2-normalise.
  2. Build a segment-text TF-IDF matrix (real descriptions) for cluster labels.
  3. Galaxy level: K-Means with k chosen by silhouette over k=8..16 (seeded).
  4. Subcluster level: HDBSCAN inside each galaxy (K-Means fallback),
     noise points attached to their nearest subcluster centroid.
  5. Labels: top TF-IDF terms per cluster — purely data-derived.
  6. Layout: classical MDS on galaxy centroids (semantic proximity between
     galaxies), classical MDS on subcluster centroids inside each galaxy, and
     a per-subcluster TruncatedSVD for the actual segment points.
     Galaxy / subcluster radii grow with sqrt(count).
  7. Cache everything to data/explore/universe_v1.json (reproducible: seed 42).

The API serves this cache; nothing is recomputed on page load.
"""
from __future__ import annotations

import json
import math
import time

import numpy as np

from app import config, db

SEED = 42
GALAXY_K_RANGE = range(8, 25)
BASE_RADIUS = 9.0            # world units per sqrt(segment count)
OUT = config.DATA_DIR / "explore" / "universe_v1.json"
REPORT = config.DATA_DIR / "explore" / "universe_report.json"
EMBED_DIR = config.DATA_DIR / "embeddings" / "sentence-transformers_all-MiniLM-L6-v2"


# --------------------------------------------------------------------------- utils
def _l2_normalize(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n < 1e-9] = 1.0
    return m / n


def _classical_mds(d: np.ndarray) -> np.ndarray:
    """Deterministic classical (Torgerson) MDS → 2D coordinates."""
    n = d.shape[0]
    d2 = d ** 2
    j = np.eye(n) - 1.0 / n
    b = -0.5 * j @ d2 @ j
    evals, evecs = np.linalg.eigh(b)
    order = np.argsort(evals)[::-1]
    evals, evecs = evals[order], evecs[:, order]
    pos = evecs[:, :2] * np.sqrt(np.clip(evals[:2], 0.0, None))
    for axis in range(2):                      # fix eigenvector sign flips
        col = pos[:, axis]
        nz = np.flatnonzero(np.abs(col) > 1e-9)
        if nz.size and col[nz[0]] < 0:
            pos[:, axis] *= -1
    return pos


def _relax(circles: list[tuple[np.ndarray, float]], bounds: float | None,
           iters: int = 200) -> np.ndarray:
    """Push overlapping circles apart (optionally keeping |c| <= bounds)."""
    pos = np.array([c for c, _ in circles])
    r = np.array([rad for _, rad in circles])
    n = len(pos)
    for _ in range(iters):
        moved = False
        for i in range(n):
            for j in range(i + 1, n):
                delta = pos[i] - pos[j]
                dist = float(np.linalg.norm(delta))
                min_d = (r[i] + r[j]) * 1.08
                if dist < 1e-6:
                    delta = np.array([1.0, 0.0]) if i % 2 else np.array([0.0, 1.0])
                    dist = 1e-6
                if dist < min_d:
                    push = (min_d - dist) * 0.5 * (delta / dist)
                    pos[i] += push
                    pos[j] -= push
                    moved = True
            if bounds is not None:
                norm = float(np.linalg.norm(pos[i]))
                if norm > bounds:
                    pos[i] *= bounds / norm
                    moved = True
        if not moved:
            break
    return pos


def _top_terms(tfidf, names: np.ndarray, rows: list[int], k: int) -> list[str]:
    mean = np.asarray(tfidf[rows].mean(axis=0)).ravel()
    order = np.argsort(mean)[::-1]
    out: list[str] = []
    for idx in order:
        t = str(names[idx])
        if len(t) < 3 or t in out:
            continue
        out.append(t)
        if len(out) >= k:
            break
    return out


def _label_from(terms: list[str], n_terms: int = 2) -> str:
    """Readable label: prefer a meaningful bigram, else top unigrams."""
    if not terms:
        return "Untitled"
    bigrams = [t for t in terms[:n_terms + 2] if " " in t]
    picked = [bigrams[0]] if bigrams else [terms[0]]
    for t in terms[1:]:
        if len(picked) >= n_terms:
            break
        if t not in picked and " " not in t:
            picked.append(t)
    return " ".join(w.capitalize() for w in " ".join(picked).split())


# --------------------------------------------------------------------------- load
def _load_segment_matrix(progress=None):
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT annotation_id, segment_id FROM annotations "
            "WHERE vector_status='indexed' AND status='active'"
        ).fetchall()
    finally:
        conn.close()
    ann_to_seg = {r["annotation_id"]: r["segment_id"] for r in rows}
    if not ann_to_seg:
        raise RuntimeError("no indexed annotations — run embed/index first")

    seg_ids = sorted(set(ann_to_seg.values()))
    seg_index = {s: i for i, s in enumerate(seg_ids)}
    total = len(ann_to_seg)

    acc = np.zeros((len(seg_ids), 384), dtype=np.float64)
    cnt = np.zeros(len(seg_ids), dtype=np.int64)
    seen = 0
    for shard in sorted(EMBED_DIR.glob("shard_*.npz")):
        with np.load(shard) as z:
            ids, vecs = z["ids"], z["vectors"]
            for aid, vec in zip(ids, vecs):
                seg = ann_to_seg.get(str(aid))
                if seg is None:
                    continue
                i = seg_index[seg]
                acc[i] += vec
                cnt[i] += 1
            seen += len(ids)
            if progress:
                progress(min(seen / max(total, 1), 1.0) * 0.4,
                         f"loaded {min(seen, total)}/{total} vectors")

    alive = cnt > 0
    seg_ids = [s for s, a in zip(seg_ids, alive) if a]
    matrix = _l2_normalize((acc[alive] / cnt[alive, None]).astype(np.float32))

    conn = db.connect()
    try:
        text_rows = conn.execute(
            "SELECT segment_id, description_english FROM annotations "
            "WHERE vector_status='indexed' AND status='active' "
            "AND description_english IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    texts: dict[str, list[str]] = {}
    for r in text_rows:
        texts.setdefault(r["segment_id"], []).append(r["description_english"])
    doc_texts = [" ".join(dict.fromkeys(texts.get(s, []))) for s in seg_ids]
    return seg_ids, matrix, doc_texts


# --------------------------------------------------------------------------- main
def run(refresh: bool = False, progress=None) -> dict:
    db.init_db()
    if OUT.exists() and not refresh:
        doc = json.loads(OUT.read_text(encoding="utf-8"))
        return {"segments": doc.get("count"), "galaxies": doc.get("galaxy_count"),
                "subclusters": doc.get("subcluster_count"), "recomputed": False}

    started = time.time()
    if progress:
        progress(0.0, "loading embedding shards")
    seg_ids, matrix, doc_texts = _load_segment_matrix(progress)
    n = len(seg_ids)
    if n < 100:
        raise RuntimeError(f"only {n} segments with vectors — run the embed stage first")

    # ---- TF-IDF for labels (real descriptions only)
    if progress:
        progress(0.45, "building TF-IDF for cluster labels")
    from sklearn.feature_extraction.text import TfidfVectorizer

    tfidf = TfidfVectorizer(stop_words="english", max_features=12000,
                            ngram_range=(1, 2), min_df=2, sublinear_tf=True)
    tfidf_mat = tfidf.fit_transform(doc_texts)
    feature_names = np.array(tfidf.get_feature_names_out())

    # ---- galaxy level: K-Means, k chosen by silhouette (seeded → reproducible)
    if progress:
        progress(0.55, "choosing galaxy count by silhouette")
    from sklearn.cluster import KMeans
    from sklearn.metrics import silhouette_score

    best_k, best_score, best_model = None, -1.0, None
    for k in GALAXY_K_RANGE:
        km = KMeans(n_clusters=k, random_state=SEED, n_init=10)
        labels = km.fit_predict(matrix)
        if len(set(labels)) < 2:
            continue
        score = float(silhouette_score(matrix, labels, metric="cosine"))
        if score > best_score:
            best_k, best_score, best_model = k, score, km
    if best_model is None:
        raise RuntimeError("galaxy clustering failed")
    galaxy_labels = best_model.labels_

    # ---- subcluster level: HDBSCAN inside each galaxy (K-Means fallback)
    if progress:
        progress(0.68, "discovering subclusters (HDBSCAN)")
    from sklearn.cluster import HDBSCAN

    sub_member_rows: list[list[int]] = []
    sub_galaxy: list[int] = []
    for g in range(best_k):
        rows = np.flatnonzero(galaxy_labels == g)
        member_vecs = matrix[rows]
        sub_labels = None
        if len(rows) >= 40:
            min_cs = max(8, int(len(rows) * 0.07))
            cand = HDBSCAN(min_cluster_size=min_cs, min_samples=3).fit_predict(member_vecs)
            if len(set(cand) - {-1}) >= 2:
                unique = [u for u in sorted(set(cand)) if u != -1]
                centers = _l2_normalize(np.vstack([
                    member_vecs[cand == u].mean(axis=0, keepdims=True) for u in unique
                ]))
                for i, lab in enumerate(cand):          # attach noise → nearest sub
                    if lab == -1:
                        cand[i] = unique[int(np.argmax(member_vecs[i] @ centers.T))]
                sub_labels = cand
        if sub_labels is None:
            kk = int(min(max(round(math.sqrt(len(rows) / 22)), 2), 6))
            kk = min(kk, max(2, len(rows) // 6))
            sub_labels = KMeans(n_clusters=kk, random_state=SEED,
                                n_init=10).fit_predict(member_vecs)
        for ui in sorted(set(int(x) for x in sub_labels)):
            members = rows[sub_labels == ui]
            sub_member_rows.append(list(members))
            sub_galaxy.append(g)

    # ---- labels from real text
    if progress:
        progress(0.78, "naming clusters from TF-IDF terms")
    galaxies: list[dict] = []
    for g in range(best_k):
        grows = np.flatnonzero(galaxy_labels == g).tolist()
        terms = _top_terms(tfidf_mat, feature_names, grows, 8)
        galaxies.append({
            "id": f"g{g:02d}",
            "label": _label_from(terms, 3),
            "keywords": terms,
            "count": len(grows),
            "rows": grows,
        })
    subclusters: list[dict] = []
    for si, members in enumerate(sub_member_rows):
        terms = _top_terms(tfidf_mat, feature_names, members, 6)
        subclusters.append({
            "galaxy": sub_galaxy[si],
            "size": len(members),
            "label": _label_from(terms, 2),
            "keywords": terms,
        })

    # ---- layout: MDS on galaxy centroids (semantic placement)
    if progress:
        progress(0.84, "laying out galaxies (classical MDS)")
    g_centroids = _l2_normalize(np.vstack(
        [matrix[g["rows"]].mean(axis=0) for g in galaxies]))
    d_gal = np.arccos(np.clip(g_centroids @ g_centroids.T, -1.0, 1.0))
    pos_gal = _classical_mds(d_gal)

    radii = [BASE_RADIUS * math.sqrt(max(g["count"], 1)) for g in galaxies]
    spread = float(np.linalg.norm(pos_gal.max(axis=0) - pos_gal.min(axis=0))) or 1.0
    pos_gal = pos_gal / spread
    m = len(galaxies)
    pair_need = [(radii[i] + radii[j]) * 1.12 for i in range(m) for j in range(i + 1, m)]
    unit_d = [float(np.linalg.norm(pos_gal[i] - pos_gal[j]))
              for i in range(m) for j in range(i + 1, m)]
    scale = np.mean(pair_need) / max(np.mean(unit_d), 1e-6)
    pos_gal = _relax([(pos_gal[i] * scale, radii[i]) for i in range(m)],
                     bounds=None, iters=250)

    # ---- subcluster placement inside each galaxy
    if progress:
        progress(0.90, "placing subclusters")
    sub_index_by_galaxy: dict[int, list[int]] = {}
    for si, g in enumerate(sub_galaxy):
        sub_index_by_galaxy.setdefault(g, []).append(si)

    sub_pos: dict[int, np.ndarray] = {}
    sub_radius: dict[int, float] = {}
    sub_local: dict[int, int] = {}           # global sub idx → per-galaxy idx
    for g, idxs in sub_index_by_galaxy.items():
        for k, si in enumerate(idxs):
            sub_local[si] = k
        R = radii[g]
        cents = _l2_normalize(np.vstack(
            [matrix[sub_member_rows[si]].mean(axis=0) for si in idxs]))
        d = np.arccos(np.clip(cents @ cents.T, -1.0, 1.0))
        p = _classical_mds(d)
        sp = float(np.linalg.norm(p.max(axis=0) - p.min(axis=0))) or 1.0
        p = p / sp
        rs = [max(R * math.sqrt(subclusters[si]["size"] /
                                max(galaxies[g]["count"], 1)) * 0.78, 5.0)
              for si in idxs]
        if len(idxs) > 1:
            pair = [(rs[i] + rs[j]) * 1.14
                    for i in range(len(idxs)) for j in range(i + 1, len(idxs))]
            ud = [float(np.linalg.norm(p[i] - p[j]))
                  for i in range(len(idxs)) for j in range(i + 1, len(idxs))]
            p = p * (np.mean(pair) / max(np.mean(ud), 1e-6))
        p = _relax([(p[i], rs[i]) for i in range(len(idxs))],
                   bounds=R * 0.74, iters=200)
        for k, si in enumerate(idxs):
            sub_pos[si] = pos_gal[g] + p[k]
            sub_radius[si] = float(rs[k])

    # ---- segment points: per-subcluster SVD, scaled into the subcluster disc
    if progress:
        progress(0.94, "projecting segment points")
    from sklearn.decomposition import TruncatedSVD

    points: list[dict] = []
    for si, members in enumerate(sub_member_rows):
        center = sub_pos[si]
        rad = sub_radius[si]
        member_vecs = matrix[members]
        if len(members) >= 3:
            xy = TruncatedSVD(n_components=2, random_state=SEED).fit_transform(member_vecs)
        else:
            xy = np.zeros((len(members), 2))
        r = np.linalg.norm(xy, axis=1)
        d_ref = float(np.percentile(r, 92)) or 1.0
        xy = xy / d_ref * (rad * 0.92)
        for k, row in enumerate(members):
            points.append({
                "id": seg_ids[row],
                "g": sub_galaxy[si],
                "s": sub_local[si],
                "x": round(float(center[0] + xy[k][0]), 3),
                "y": round(float(center[1] + xy[k][1]), 3),
                "z": round(float(min(r[k] / d_ref, 1.0)), 3),
            })

    # ---- assemble cache document
    for g in galaxies:
        gi = int(g["id"][1:])
        g["hue"] = round((gi * 137.508) % 360, 1)
        g["cx"] = round(float(pos_gal[gi][0]), 3)
        g["cy"] = round(float(pos_gal[gi][1]), 3)
        g["radius"] = round(radii[gi], 2)
        g["centroid"] = [round(float(v), 5) for v in g_centroids[gi]]
        del g["rows"]
        g["subclusters"] = [{
            "id": f"{g['id']}s{sub_local[si]:02d}",
            "label": subclusters[si]["label"],
            "keywords": subclusters[si]["keywords"],
            "count": subclusters[si]["size"],
            "cx": round(float(sub_pos[si][0]), 3),
            "cy": round(float(sub_pos[si][1]), 3),
            "radius": round(sub_radius[si], 2),
            "centroid": [round(float(v), 5) for v in _l2_normalize(
                matrix[sub_member_rows[si]].mean(axis=0, keepdims=True))[0]],
        } for si in sub_index_by_galaxy.get(gi, [])]

    doc = {
        "version": 1,
        "model": "sentence-transformers/all-MiniLM-L6-v2",
        "seed": SEED,
        "count": len(points),
        "galaxy_count": len(galaxies),
        "subcluster_count": len(subclusters),
        "silhouette": round(best_score, 4),
        "chosen_k": best_k,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "generator": "worker/stages/universe.py (KMeans+HDBSCAN+MDS+SVD)",
        "galaxies": galaxies,
        "points": points,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    report = {
        "segments": len(points),
        "galaxies": len(galaxies),
        "subclusters": len(subclusters),
        "chosen_k": best_k,
        "silhouette_cosine": round(best_score, 4),
        "labels": {g["id"]: {"label": g["label"], "count": g["count"],
                             "subclusters": len(g["subclusters"])} for g in galaxies},
        "cache": str(OUT),
        "elapsed_s": round(time.time() - started, 1),
        "generated_at": doc["created_at"],
    }
    REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    conn = db.connect()
    try:
        db.set_meta(conn, "universe_report", json.dumps(report, ensure_ascii=False))
        conn.commit()
    finally:
        conn.close()

    if progress:
        progress(1.0, f"{len(galaxies)} galaxies, {len(subclusters)} subclusters cached")
    return report
