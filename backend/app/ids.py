"""Deterministic identifiers and small shared helpers."""
from __future__ import annotations

import uuid

# Stable namespace so every run produces the same ids (idempotent ingestion).
NAMESPACE = uuid.UUID("6f1d0f4e-3a7a-4f2f-9d3b-1f4c2b8a9e01")


def segment_id(video_id: str, start_s: int, end_s: int) -> str:
    """Segment identity = video_id + start + end (spec §3/§5)."""
    return f"{video_id}:{start_s}-{end_s}"


def file_slug(video_id: str, start_s: int, end_s: int) -> str:
    """Filesystem-safe stem matching the source filename convention."""
    return f"{video_id}_{start_s}_{end_s}"


def annotation_id(csv_row: int) -> str:
    return f"a{csv_row:06d}"


def point_id(seg_id: str, ann_id: str) -> str:
    """Qdrant uuid5 point id — stable across re-indexing."""
    return str(uuid.uuid5(NAMESPACE, f"{seg_id}|{ann_id}"))


def uuid_name(kind: str, key: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{kind}|{key}"))


def ms_to_clock(ms: int) -> str:
    s = int(ms) // 1000
    return f"{s // 60:02d}:{s % 60:02d}"
