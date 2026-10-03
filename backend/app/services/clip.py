"""Local CLIP vision/text embeddings (laion CLIP-ViT-B/32) — no cloud APIs.

One shared, normalized embedding space for:
- frame indexing (worker/stages/visual.py)
- image search (reference image -> scene vectors)
- text-to-frame retrieval for scenes that have no text annotations
  (uploaded videos) — ARCHITECTURE §2 "Same compatible embedding space".

The vector dimension is read from the model configuration at runtime
(never hardcoded), mirroring services/embeddings.py.
"""
from __future__ import annotations

import base64
import io
import threading
from pathlib import Path
from typing import Sequence

import numpy as np

from .. import config

_lock = threading.Lock()
_models: dict[str, tuple] = {}  # name -> (model, processor)


def load_model(model_name: str | None = None):
    from transformers import CLIPModel, CLIPProcessor

    name = model_name or config.CLIP_MODEL
    with _lock:
        entry = _models.get(name)
        if entry is None:
            processor = CLIPProcessor.from_pretrained(name)
            model = CLIPModel.from_pretrained(name)
            model.eval()
            entry = (model, processor)
            _models[name] = entry
        return entry


def model_info(model_name: str | None = None) -> dict:
    name = model_name or config.CLIP_MODEL
    model, _ = load_model(name)
    dim = int(getattr(model.config, "projection_dim", 0) or 0)
    if not dim:
        raise RuntimeError(f"CLIP model {name} did not report a projection dimension")
    version = getattr(model.config, "_commit_hash", None) or "local"
    return {"model": name, "dim": dim, "version": version}


def _normalize(arr: np.ndarray) -> np.ndarray:
    a = np.asarray(arr, dtype=np.float32)
    norms = np.linalg.norm(a, axis=-1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    return (a / norms).astype(np.float32)


def _empty(dim: int) -> np.ndarray:
    return np.zeros((0, dim), dtype=np.float32)


def encode_images(
    images: Sequence, model_name: str | None = None, batch_size: int | None = None
) -> np.ndarray:
    """PIL RGB images -> L2-normalized CLIP image embeddings (N, dim)."""
    import torch

    name = model_name or config.CLIP_MODEL
    model, processor = load_model(name)
    if not images:
        return _empty(model_info(name)["dim"])
    bs = batch_size or config.CLIP_BATCH_SIZE
    feats: list[np.ndarray] = []
    with torch.no_grad():
        for i in range(0, len(images), bs):
            batch = list(images[i:i + bs])
            inputs = processor(images=batch, return_tensors="pt")
            out = model.get_image_features(**inputs)
            feats.append(out.cpu().numpy())
    return _normalize(np.vstack(feats))


def encode_image_paths(
    paths: Sequence[str | Path], model_name: str | None = None
) -> tuple[np.ndarray, list[str]]:
    """Frame files on disk -> (vectors, kept_paths). Unreadable files are skipped."""
    from PIL import Image

    imgs = []
    kept: list[str] = []
    for p in paths:
        p = str(p)
        try:
            with Image.open(p) as im:
                imgs.append(im.convert("RGB"))
            kept.append(p)
        except Exception:  # noqa: BLE001 — one bad frame must not kill the batch
            continue
    if not imgs:
        return _empty(model_info(model_name)["dim"]), []
    return encode_images(imgs, model_name=model_name), kept


def decode_image_bytes(data: bytes) -> "object":
    """Bytes -> PIL RGB image; raises ValueError when unreadable."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.convert("RGB")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"not a decodable image: {exc}") from exc


def encode_image_bytes(data: bytes, model_name: str | None = None) -> np.ndarray:
    """Query-image bytes (JPEG/PNG/WebP/GIF) -> (1, dim) normalized vector."""
    return encode_images([decode_image_bytes(data)], model_name=model_name)


def image_from_base64(payload: str, max_bytes: int | None = None) -> bytes:
    """data-URL or raw base64 -> validated raw bytes (size + magic, §15)."""
    data = (payload or "").strip()
    if "," in data and data[:5].lower().startswith("data:"):
        data = data.split(",", 1)[1]
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as exc:  # noqa: BLE001 — binascii.Error etc.
        raise ValueError("image_base64 is not valid base64") from exc
    limit = max_bytes if max_bytes is not None else config.MAX_IMAGE_BYTES
    if len(raw) > limit:
        raise ValueError(f"image exceeds {limit} byte limit")
    if not raw:
        raise ValueError("empty image")
    ok = False
    for magic, mime in config.ALLOWED_IMAGE_MAGIC.items():
        if raw.startswith(magic):
            if mime == "image/webp":
                ok = raw[8:12] == b"WEBP"
            else:
                ok = True
            break
    if not ok:
        raise ValueError("unsupported image type (need JPEG, PNG, WebP or GIF)")
    return raw


def encode_texts(texts: Sequence[str], model_name: str | None = None) -> np.ndarray:
    """Text -> L2-normalized CLIP text embeddings (same space as frames)."""
    import torch

    name = model_name or config.CLIP_MODEL
    model, processor = load_model(name)
    if not texts:
        return _empty(model_info(name)["dim"])
    with torch.no_grad():
        inputs = processor(
            text=list(texts), return_tensors="pt", padding=True, truncation=True
        )
        out = model.get_text_features(**inputs)
    return _normalize(out.cpu().numpy())


def encode_text(text: str, model_name: str | None = None) -> np.ndarray:
    return encode_texts([text], model_name=model_name)[0]
