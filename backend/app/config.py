"""ReelMind configuration — all local, no cloud services."""
from __future__ import annotations

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    # worker/ stages are shared between the CLI worker and the API
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = Path(os.environ.get("REELMIND_DATA_DIR", PROJECT_ROOT / "data"))
MEDIA_DIR = Path(os.environ.get("REELMIND_MEDIA_DIR", PROJECT_ROOT / "media"))
THUMBNAIL_DIR = MEDIA_DIR / "thumbnails"
PREVIEW_DIR = MEDIA_DIR / "previews"
ROUGHCUT_DIR = MEDIA_DIR / "roughcuts"
FRAME_DIR = MEDIA_DIR / "frames"          # CLIP keyframes (vision embeddings)
UPLOAD_DIR = MEDIA_DIR / "uploads"        # user-uploaded source videos (immutable)

DB_PATH = Path(os.environ.get("REELMIND_DB_PATH", DATA_DIR / "reelmind.db"))

DATASET_CSV = Path(
    os.environ.get(
        "REELMIND_DATASET_CSV",
        PROJECT_ROOT / "MSR Video Description Corpus.csv",
    )
)
VIDEO_DIR = Path(
    os.environ.get("REELMIND_VIDEO_DIR", PROJECT_ROOT / "YouTubeClips" / "YouTubeClips")
)

# Vector store (local Qdrant, no API key)
QDRANT_URL = os.environ.get("REELMIND_QDRANT_URL", "http://localhost:6333")
# Text collection sized for the local 384-dim model.
QDRANT_COLLECTION = os.environ.get("REELMIND_QDRANT_COLLECTION", "reelmind_minilm384_v1")

# Vision embeddings (CLIP) — Image Search phase (ARCHITECTURE §2/§5/§6).
# The pre-existing scenes-clip-vitb32-laion2b-v1 collection (512-dim Cosine) is
# now the ACTIVE frame collection: written only by the vision path
# (services/visualstore.py, worker/stages/visual.py), never by the text path.
CLIP_MODEL = os.environ.get(
    "REELMIND_CLIP_MODEL", "laion/CLIP-ViT-B-32-laion2B-s34B-b79K"
)
CLIP_COLLECTION = os.environ.get(
    "REELMIND_CLIP_COLLECTION", "scenes-clip-vitb32-laion2b-v1"
)
CLIP_BATCH_SIZE = int(os.environ.get("REELMIND_CLIP_BATCH_SIZE", "64"))
FRAMES_PER_SEGMENT = int(os.environ.get("REELMIND_FRAMES_PER_SEGMENT", "2"))
# The text path must never write the CLIP collection (dim-mismatch guard in
# vectorstore.ensure_collection backs this up). Kept as a tuple for reports.
LEGACY_COLLECTIONS = (CLIP_COLLECTION,)

# Local embedding model (no embedding API)
EMBEDDING_MODEL = os.environ.get(
    "REELMIND_EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
EMBEDDING_BATCH_SIZE = int(os.environ.get("REELMIND_EMBEDDING_BATCH_SIZE", "256"))
VECTOR_VERSION = 1  # bump to invalidate/recompare vectors (embedding_meta §4)

# Upload validation limits (ARCHITECTURE §15)
MAX_UPLOAD_BYTES = int(os.environ.get(
    "REELMIND_MAX_UPLOAD_BYTES", str(500 * 1024 * 1024)))          # 500 MB / video
MAX_IMAGE_BYTES = int(os.environ.get(
    "REELMIND_MAX_IMAGE_BYTES", str(15 * 1024 * 1024)))            # 15 MB / query image
MAX_UPLOAD_DURATION_MS = int(os.environ.get(
    "REELMIND_MAX_UPLOAD_DURATION_MS", str(30 * 60 * 1000)))       # 30 min / video
ALLOWED_VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
ALLOWED_IMAGE_MIME = {"image/jpeg", "image/png", "image/webp", "image/gif"}
ALLOWED_IMAGE_MAGIC = {                    # validated server-side, never trust MIME
    b"\xff\xd8\xff": "image/jpeg",
    b"\x89PNG": "image/png",
    b"GIF8": "image/gif",
    b"RIFF": "image/webp",                 # RIFF....WEBP checked in code
}

# Local translation provider (no cloud translation API).
# "ollama" = local Ollama at localhost:11434; "none" = skip translation batch.
TRANSLATION_PROVIDER = os.environ.get("REELMIND_TRANSLATION_PROVIDER", "ollama")
OLLAMA_URL = os.environ.get("REELMIND_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("REELMIND_OLLAMA_MODEL", "qwen:latest")

# Deferred feature — VLM captioning (BLIP etc.). CLIP embedding for Image
# Search is IMPLEMENTED via CLIP_MODEL/CLIP_COLLECTION above; only free-text
# captioning of uploaded frames stays behind this switch.
IMAGE_VLM_PROVIDER = os.environ.get("REELMIND_IMAGE_VLM_PROVIDER", "disabled")

SEGMENTATION_VERSION = 1
SEARCH_OVERFETCH = 6  # annotations fetched per requested segment slot
MAX_TOP_K = 100


def ensure_dirs() -> None:
    for d in (DATA_DIR, THUMBNAIL_DIR, PREVIEW_DIR, ROUGHCUT_DIR,
              FRAME_DIR, UPLOAD_DIR):
        d.mkdir(parents=True, exist_ok=True)
