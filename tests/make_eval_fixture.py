"""Build tests/queries.json — retrieval evaluation fixture.

Exact-text queries: sampled English annotations whose own segment must rank top-1.
Paraphrase queries: hand-written wording variations whose ground truth is every
segment containing the given keyword (ground truth construction only; retrieval
itself never uses keywords).
"""

import json
import random
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "reelmind.db"
OUT = Path(__file__).resolve().parent / "queries.json"

PARAPHRASES = [
    {"query": "a guy driving a car on the road", "keyword": "driving"},
    {"query": "people cheering for the team", "keyword": "cheering"},
    {"query": "somebody typing on a laptop keyboard", "keyword": "typing"},
    {"query": "heavy rain falling on the street", "keyword": "rain"},
    {"query": "a dog running across the yard", "keyword": "dog"},
    {"query": "sunset over the ocean", "keyword": "sunset"},
    {"query": "a crowd watching the show", "keyword": "crowd"},
    {"query": "children playing in the park", "keyword": "children playing"},
    {"query": "chef preparing food in the kitchen", "keyword": "kitchen"},
    {"query": "a person laughing at the joke", "keyword": "laughing"},
]

EXACT_SQL = """
    SELECT a.segment_id, a.description_english
    FROM annotations a
    JOIN segments s ON s.segment_id = a.segment_id
    WHERE a.detected_language = 'en'
      AND a.status = 'active'
      AND a.is_duplicate = 0
      AND a.vector_status = 'indexed'
      AND s.status = 'ready'
    ORDER BY a.annotation_id
"""


def main() -> None:
    conn = sqlite3.connect(DB)
    random.seed(7)

    rows = conn.execute(EXACT_SQL).fetchall()
    exact = random.sample(rows, 40)
    queries = [
        {"query": text, "relevant_ids": [seg], "kind": "exact"}
        for seg, text in exact
    ]

    for p in PARAPHRASES:
        ids = [
            r[0]
            for r in conn.execute(
                """SELECT DISTINCT a.segment_id FROM annotations a
                   JOIN segments s ON s.segment_id = a.segment_id
                   WHERE a.status = 'active' AND s.status = 'ready'
                     AND lower(a.description_english) LIKE ? LIMIT 40""",
                (f"%{p['keyword']}%",),
            ).fetchall()
        ]
        if ids:
            queries.append(
                {"query": p["query"], "relevant_ids": ids, "kind": "paraphrase"}
            )

    conn.close()
    OUT.write_text(json.dumps(queries, indent=2), encoding="utf-8")
    print(f"wrote {OUT} with {len(queries)} queries")


if __name__ == "__main__":
    main()
