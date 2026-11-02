"""Singleton shared FastEmbed multilingual encoder for ChromaDB and Semantic Router.

Ensures `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` is loaded once
in process memory and reused by both the policy vector store and local intent guardrails.
"""

import numpy as np
from chromadb.api.types import Documents, EmbeddingFunction, Embeddings
from fastembed import TextEmbedding
from semantic_router.encoders import FastEmbedEncoder

DEFAULT_MULTILINGUAL_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_EMBEDDING_CACHE: dict[str, TextEmbedding] = {}


def get_shared_text_embedding(
    model_name: str = DEFAULT_MULTILINGUAL_MODEL,
) -> TextEmbedding:
    """Load and cache a single ONNX `TextEmbedding` model instance in RAM."""
    resolved_name = model_name or DEFAULT_MULTILINGUAL_MODEL
    cached = _EMBEDDING_CACHE.get(resolved_name)
    if cached is None:
        cached = TextEmbedding(model_name=resolved_name)
        _EMBEDDING_CACHE[resolved_name] = cached
    return cached


class SharedChromaEmbeddingFunction(EmbeddingFunction[Documents]):
    """ChromaDB 1.x compatible embedding adapter backed by the shared FastEmbed model."""

    def __init__(self, model_name: str = DEFAULT_MULTILINGUAL_MODEL) -> None:
        self._model_name = model_name
        self._model = get_shared_text_embedding(model_name)

    @staticmethod
    def name() -> str:
        """Return unique identifier required by ChromaDB 1.x `EmbeddingFunction`."""
        return "shared-fastembed-multilingual"

    def get_config(self) -> dict[str, object]:
        """Serialize configuration for ChromaDB 1.x metadata."""
        return {"model_name": self._model_name}

    @staticmethod
    def build_from_config(config: dict[str, object]) -> "SharedChromaEmbeddingFunction":
        """Reconstruct embedding function from ChromaDB collection metadata."""
        model_name = str(config.get("model_name") or DEFAULT_MULTILINGUAL_MODEL)
        return SharedChromaEmbeddingFunction(model_name=model_name)

    def __call__(self, input: Documents) -> Embeddings:  # noqa: A002
        vectors = list(self._model.embed(list(input)))
        return [np.asarray(vec, dtype=np.float32) for vec in vectors]


class SharedFastEmbedEncoder(FastEmbedEncoder):
    """`semantic-router` encoder subclass reusing the cached singleton `TextEmbedding`."""

    name: str = DEFAULT_MULTILINGUAL_MODEL
    max_length: int = 512

    def _initialize_client(self) -> TextEmbedding:
        """Return the process-wide cached ONNX `TextEmbedding` instance."""
        return get_shared_text_embedding(self.name)
