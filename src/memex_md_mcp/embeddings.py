"""Embedding model loading and text embedding."""

import logging
import threading

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "google/embeddinggemma-300m"
EMBEDDING_DIM = 768
IDLE_UNLOAD_SECONDS = 300  # 5 minutes

_model: SentenceTransformer | None = None
_unload_timer: threading.Timer | None = None
_lock = threading.Lock()

logger = logging.getLogger(__name__)


def _unload_model() -> None:
    """Unload the model to free memory."""
    global _model, _unload_timer
    with _lock:
        if _model is not None:
            logger.info("Unloading embedding model after idle timeout")
            _model = None
        _unload_timer = None


def _reset_unload_timer() -> None:
    """Reset the idle unload timer."""
    global _unload_timer
    if _unload_timer is not None:
        _unload_timer.cancel()
    _unload_timer = threading.Timer(IDLE_UNLOAD_SECONDS, _unload_model)
    _unload_timer.daemon = True  # Don't block process exit
    _unload_timer.start()


def get_model() -> SentenceTransformer:
    """Lazy-load the embedding model. Unloads after idle timeout to free memory."""
    global _model
    with _lock:
        if _model is None:
            logger.info("Loading embedding model")
            _model = SentenceTransformer(MODEL_NAME)
        _reset_unload_timer()
        return _model


def embed_text(text: str) -> np.ndarray:
    """Embed a single text string. Returns normalized float32 array of shape (768,)."""
    model = get_model()
    return model.encode(text, convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed multiple texts. Returns normalized float32 array of shape (n, 768)."""
    model = get_model()
    return model.encode(texts, convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)
