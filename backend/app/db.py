"""SQLite access helpers."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import config

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or config.DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created before a column existed."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(annotations)")}
    for col, ddl in (
        ("detector_raw", "TEXT"),
        ("detector_confidence", "REAL"),
        ("language_rule", "TEXT"),
    ):
        if col not in cols:
            conn.execute(f"ALTER TABLE annotations ADD COLUMN {col} {ddl}")
    vcols = {r["name"] for r in conn.execute("PRAGMA table_info(videos)")}
    for col, ddl in (
        ("sha256", "TEXT"),            # upload idempotency key (§15)
        ("original_name", "TEXT"),     # sanitized source filename
        ("file_path", "TEXT"),         # immutable stored upload
        ("duration_ms", "INTEGER"),    # ffprobe result
    ):
        if col not in vcols:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {col} {ddl}")
    conn.commit()


def init_db(db_path: Path | None = None) -> None:
    config.ensure_dirs()
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def transaction(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
