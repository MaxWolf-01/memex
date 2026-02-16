"""Embedding model loading and text embedding."""

from __future__ import annotations

import contextlib
import logging
import os
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None
_model_name: str | None = None


@contextlib.contextmanager
def _quiet_model_load():
    """Suppress tqdm progress bars and verbose weight-loading logs during model init."""
    old_tqdm = os.environ.get("TQDM_DISABLE")
    os.environ["TQDM_DISABLE"] = "1"
    noisy = ["transformers", "sentence_transformers", "safetensors"]
    saved = {n: logging.getLogger(n).level for n in noisy}
    for n in noisy:
        logging.getLogger(n).setLevel(logging.WARNING)
    try:
        yield
    finally:
        if old_tqdm is None:
            os.environ.pop("TQDM_DISABLE", None)
        else:
            os.environ["TQDM_DISABLE"] = old_tqdm
        for n, lvl in saved.items():
            logging.getLogger(n).setLevel(lvl)


def get_model(model_name: str) -> SentenceTransformer:
    """Load the specified model. Caches one model at a time — reloads on name change."""
    global _model, _model_name
    if _model is None or _model_name != model_name:
        # Import inside the quiet context so TQDM_DISABLE is set before tqdm's
        # envwrap decorator captures the environment at import time.
        with _quiet_model_load():
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model: %s", model_name)
            _model = SentenceTransformer(model_name)
        _model_name = model_name
    return _model


def get_embedding_dim(model_name: str) -> int:
    """Get the embedding dimension for a model (loads the model if needed)."""
    model = get_model(model_name)
    dim = model.get_sentence_embedding_dimension()
    assert dim is not None, f"Model {model_name} returned None for embedding dimension"
    return dim


def embed_text(text: str, model_name: str) -> np.ndarray:
    """Embed a single text string. Returns normalized float32 array."""
    model = get_model(model_name)
    return model.encode(
        text, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
    ).astype(np.float32)


def embed_texts(texts: list[str], model_name: str, batch_size: int = 8) -> np.ndarray:
    """Embed multiple texts. Returns normalized float32 array of shape (n, dim)."""
    model = get_model(model_name)
    return model.encode(
        texts, batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
    ).astype(np.float32)
