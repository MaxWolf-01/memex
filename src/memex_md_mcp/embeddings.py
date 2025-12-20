"""Embedding model loading and text embedding."""

import numpy as np
from sentence_transformers import SentenceTransformer

MODEL_NAME = "google/embeddinggemma-300m"
EMBEDDING_DIM = 768

_model: SentenceTransformer | None = None


def get_model() -> SentenceTransformer:
    """Lazy-load the embedding model."""
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_text(text: str) -> np.ndarray:
    """Embed a single text string. Returns normalized float32 array of shape (768,)."""
    model = get_model()
    return model.encode(text, convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)


def embed_texts(texts: list[str]) -> np.ndarray:
    """Embed multiple texts. Returns normalized float32 array of shape (n, 768)."""
    model = get_model()
    return model.encode(texts, convert_to_numpy=True, normalize_embeddings=True).astype(np.float32)
