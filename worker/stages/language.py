"""Phase 3 — multilingual description processing.

Detects the *actual* language of every description (declared CSV language is
not trusted), normalizes English descriptions, and optionally translates
non-English descriptions with a **local** provider. The source CSV is never
modified; derived fields live only in SQLite.
"""
from __future__ import annotations

import json
import time

from app import config, db
from app.services.language import decide_language_batch, get_provider

ENGLISH = "en"


def run(translate: bool = False, limit: int | None = None, provider_name: str | None = None,
        force: bool = False, progress=None) -> dict:
    db.init_db()
    conn = db.connect()
    try:
        # --- 1. language detection for rows not yet processed ---
        if force:
            conn.execute("UPDATE annotations SET detected_language=NULL")
            conn.commit()
        pending = conn.execute(
            "SELECT annotation_id, description_original, declared_language "
            "FROM annotations WHERE detected_language IS NULL ORDER BY csv_row"
        ).fetchall()
        if limit:
            pending = pending[:limit]

        detected = 0
        declared_mismatch = 0
        rules: dict[str, int] = {}
        lang_counts: dict[str, int] = {}
        batch = 2000
        for i in range(0, len(pending), batch):
            chunk = pending[i:i + batch]
            decisions = decide_language_batch(
                [(r["description_original"], r["declared_language"]) for r in chunk]
            )
            for row, (final, raw, conf, rule) in zip(chunk, decisions):
                lang_counts[final] = lang_counts.get(final, 0) + 1
                rules[rule] = rules.get(rule, 0) + 1
                if final == ENGLISH:
                    conn.execute(
                        "UPDATE annotations SET detected_language=?, detector_raw=?, "
                        "detector_confidence=?, language_rule=?, description_english=?, "
                        "translation_status='NOT_NEEDED' WHERE annotation_id=?",
                        (final, raw, conf, rule, row["description_original"],
                         row["annotation_id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE annotations SET detected_language=?, detector_raw=?, "
                        "detector_confidence=?, language_rule=?, "
                        "description_english=CASE WHEN translation_status='OK' "
                        "THEN description_english ELSE NULL END, "
                        "translation_status=CASE WHEN translation_status='OK' "
                        "THEN 'OK' ELSE 'PENDING' END "
                        "WHERE annotation_id=?",
                        (final, raw, conf, rule, row["annotation_id"]),
                    )
                declared_iso = (row["declared_language"] or "").strip().lower()
                declared_en = declared_iso == "english"
                if declared_en != (final == ENGLISH):
                    declared_mismatch += 1
                detected += 1
            conn.commit()
            if progress:
                progress(min(i + len(chunk), len(pending)) / max(len(pending), 1) * 0.7,
                         f"detected language on {i + len(chunk)}/{len(pending)}")

        # --- 2. optional local translation for non-English batch ---
        translated = failed = skipped = 0
        if translate:
            provider = get_provider(provider_name)
            rows = conn.execute(
                "SELECT annotation_id, description_original, detected_language "
                "FROM annotations WHERE translation_status IN ('PENDING','FAILED') "
                "AND status='active' ORDER BY csv_row"
            ).fetchall()
            if limit:
                rows = rows[:limit]
            total = len(rows)
            for i, row in enumerate(rows):
                try:
                    text = provider.translate(
                        row["description_original"], row["detected_language"] or "auto"
                    )
                    conn.execute(
                        "UPDATE annotations SET description_english=?, "
                        "translation_status='OK' WHERE annotation_id=?",
                        (text, row["annotation_id"]),
                    )
                    translated += 1
                except Exception as exc:  # noqa: BLE001 — keep row, mark FAILED (spec §7)
                    conn.execute(
                        "UPDATE annotations SET translation_status=? WHERE annotation_id=?",
                        (f"FAILED: {str(exc)[:180]}", row["annotation_id"]),
                    )
                    failed += 1
                if i % 25 == 0:
                    conn.commit()
                    if progress:
                        progress(0.7 + (i + 1) / max(total, 1) * 0.3,
                                 f"translated {i + 1}/{total}")
            conn.commit()
        else:
            skipped = conn.execute(
                "SELECT COUNT(*) c FROM annotations WHERE translation_status='PENDING'"
            ).fetchone()["c"]

        # --- summary ---
        summary = {
            "detected_total": detected,
            "declared_vs_detected_mismatch": declared_mismatch,
            "detection_rules": dict(sorted(rules.items(), key=lambda kv: -kv[1])),
            "detected_language_counts": dict(sorted(lang_counts.items(),
                                                    key=lambda kv: -kv[1])),
            "english_ready": conn.execute(
                "SELECT COUNT(*) c FROM annotations WHERE translation_status='NOT_NEEDED'"
            ).fetchone()["c"],
            "translation_enabled": bool(translate),
            "translated_ok": translated,
            "translation_failed": failed,
            "translation_pending": skipped,
            "provider": (provider_name or config.TRANSLATION_PROVIDER) if translate else None,
            "elapsed_s": None,
        }
        db.set_meta(conn, "language_report", json.dumps(summary, ensure_ascii=False))
        conn.commit()
        (config.DATA_DIR / "language_report.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return summary
    finally:
        conn.close()


if __name__ == "__main__":
    t = time.time()
    print(json.dumps(run(), indent=2, ensure_ascii=False))
    print("elapsed", round(time.time() - t, 1))
