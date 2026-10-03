"""Retrieval evaluation: Recall@1/5/10 and MRR over tests/queries.json.

Runs in-process (embedding model + Qdrant, no HTTP server needed).
Usage: python tests/eval_retrieval.py
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services import embeddings, vectorstore  # noqa: E402

QUERIES = Path(__file__).resolve().parent / "queries.json"
K_LIST = (1, 5, 10)


def main() -> None:
    queries = json.loads(QUERIES.read_text(encoding="utf-8"))
    recalls = {k: 0 for k in K_LIST}
    mrr = 0.0
    total = 0
    empty = 0

    for q in queries:
        vec = embeddings.embed_query(q["query"])
        hits = vectorstore.search(vec, top_k=10)
        ranked = [h.payload["segment_id"] for h in hits]
        relevant = set(q["relevant_ids"])
        if not ranked:
            empty += 1
        for k in K_LIST:
            if relevant.intersection(ranked[:k]):
                recalls[k] += 1
        for i, sid in enumerate(ranked, start=1):
            if sid in relevant:
                mrr += 1.0 / i
                break
        total += 1

    print(f"queries: {total}  empty_results: {empty}")
    for k in K_LIST:
        print(f"Recall@{k}: {recalls[k] / total:.3f}")
    print(f"MRR: {mrr / total:.3f}")


if __name__ == "__main__":
    main()
