"""Embedding model loading and text embedding."""

from __future__ import annotations

import contextlib
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None
_model_key: tuple[str, str | None] | None = None  # (model name, device)


def is_available() -> bool:
    """Check if sentence-transformers is installed."""
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


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


def get_model(model_name: str, device: str | None = None) -> SentenceTransformer:
    """Load the specified model on `device`; None lets sentence-transformers pick, a GPU if it finds one.

    Caches one model at a time — reloads when the name or the device changes.
    """
    global _model, _model_key
    if _model is None or _model_key != (model_name, device):
        # Import inside the quiet context so TQDM_DISABLE is set before tqdm's
        # envwrap decorator captures the environment at import time.
        with _quiet_model_load():
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for semantic search. "
                    "Install with: pip install memex-md[semantic]"
                ) from None

            logger.info("Loading embedding model: %s (device: %s)", model_name, device or "auto")
            _model = SentenceTransformer(model_name, device=device)
        _model_key = (model_name, device)
    return _model


def get_embedding_dim(model_name: str, device: str | None = None) -> int:
    """Get the embedding dimension for a model (loads the model if needed)."""
    model = get_model(model_name, device)
    dim = model.get_embedding_dimension()
    assert dim is not None, f"Model {model_name} returned None for embedding dimension"
    return dim


def embed_text(text: str, model_name: str, device: str | None = None) -> np.ndarray:
    """Embed a single text string. Returns normalized float32 array."""
    import numpy as np

    model = get_model(model_name, device)
    return model.encode(text, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False).astype(
        np.float32
    )


def embed_texts(texts: list[str], model_name: str, device: str | None = None, batch_size: int = 8) -> np.ndarray:
    """Embed multiple texts. Returns normalized float32 array of shape (n, dim)."""
    import numpy as np

    model = get_model(model_name, device)
    return model.encode(
        texts, batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False
    ).astype(np.float32)
