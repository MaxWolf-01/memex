"""Embedding model loading and text embedding."""

import logging

import numpy as np
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None
_model_name: str | None = None


def get_model(model_name: str) -> SentenceTransformer:
    """Load the specified model. Caches one model at a time — reloads on name change."""
    global _model, _model_name
    if _model is None or _model_name != model_name:
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
    return model.encode(text, convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)


def embed_texts(texts: list[str], model_name: str) -> np.ndarray:
    """Embed multiple texts. Returns normalized float32 array of shape (n, dim)."""
    model = get_model(model_name)
    return model.encode(texts, convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)
