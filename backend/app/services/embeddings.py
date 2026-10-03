"""Local embedding service — sentence-transformers, no embedding API.

The vector dimension is read from the model configuration at runtime
(never hardcoded) so a model swap cannot silently corrupt indexing.
"""
from __future__ import annotations

import threading
from typing import Sequence

import numpy as np

from .. import config

_lock = threading.Lock()
_models: dict[str, "SentenceTransformer"] = {}


def load_model(model_name: str | None = None):
    from sentence_transformers import SentenceTransformer

    name = model_name or config.EMBEDDING_MODEL
    with _lock:
        model = _models.get(name)
        if model is None:
            model = SentenceTransformer(name)
            _models[name] = model
        return model


def model_info(model_name: str | None = None) -> dict:
    name = model_name or config.EMBEDDING_MODEL
    model = load_model(name)
    dim = int(model.get_sentence_embedding_dimension())
    if not dim:
        raise RuntimeError(f"Embedding model {name} did not report a vector dimension")
    return {"model": name, "dim": dim}


def embed_texts(
    texts: Sequence[str],
    model_name: str | None = None,
    batch_size: int | None = None,
    normalize: bool = False,
) -> np.ndarray:
    if not texts:
        name = model_name or config.EMBEDDING_MODEL
        return np.zeros((0, model_info(name)["dim"]), dtype=np.float32)
    model = load_model(model_name)
    vectors = model.encode(
        list(texts),
        batch_size=batch_size or config.EMBEDDING_BATCH_SIZE,
        show_progress_bar=False,
        convert_to_numpy=True,
        normalize_embeddings=normalize,
    )
    return np.asarray(vectors, dtype=np.float32)


def embed_query(text: str, model_name: str | None = None) -> np.ndarray:
    return embed_texts([text], model_name=model_name)[0]
