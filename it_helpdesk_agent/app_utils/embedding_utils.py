"""
Embedding Utilities & Constants for Enterprise Knowledge Base.

Provides a unified embedding model identifier, fail-closed production vector generation,
and batch embedding helper for both BigQuery Vector Knowledge Store and Data Ingestion Pipeline.
"""

import os
import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Enterprise standard embedding model across ingestion and query
DEFAULT_EMBEDDING_MODEL = "text-embedding-005"
EMBEDDING_DIMENSIONS = 768


class EmbeddingGenerationError(RuntimeError):
    """Raised when Vertex AI text embedding generation fails in production (fail-closed)."""
    pass


def generate_text_embedding(
    text: str,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    use_vertex: Optional[bool] = None
) -> list[float]:
    """
    Generates a dense vector embedding for a single text query or document chunk.
    If Vertex AI is enabled (or USE_VERTEX_EMBEDDING is true), calls Vertex AI TextEmbeddingModel.
    If embedding fails in production mode, raises EmbeddingGenerationError (fail-closed).
    If use_vertex is explicitly False (offline dev/testing/dry-run), generates a normalized pseudo-vector.
    """
    if use_vertex is None:
        use_vertex = os.getenv("USE_VERTEX_EMBEDDING", "false").lower() in ("true", "1", "yes")

    if use_vertex:
        if not text.strip():
            return [0.0] * EMBEDDING_DIMENSIONS
        try:
            from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput
            embedding_model = TextEmbeddingModel.from_pretrained(model_name)
            inputs = [TextEmbeddingInput(text=text, task_type="RETRIEVAL_QUERY")]
            embeddings = embedding_model.get_embeddings(inputs)
            if embeddings and hasattr(embeddings[0], "values"):
                return list(embeddings[0].values)
            raise EmbeddingGenerationError("Vertex AI returned empty embedding response.")
        except Exception as e:
            logger.error("Vertex AI embedding failed in production mode (%s). Raising fail-closed exception.", e)
            raise EmbeddingGenerationError(f"Vertex AI embedding failed: {e}") from e

    # Local deterministic pseudo-vector for offline simulation/testing (768 dimensions)
    words = text.lower().split()
    vec = [0.0] * EMBEDDING_DIMENSIONS
    for i, w in enumerate(words[:EMBEDDING_DIMENSIONS]):
        vec[i] = float(len(w)) / 10.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def generate_batch_embeddings(
    texts: list[str],
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = 20,
    task_type: str = "RETRIEVAL_DOCUMENT",
    use_vertex: Optional[bool] = None
) -> list[list[float]]:
    """
    Generates embeddings for a batch of text chunks with chunking/batching support.
    Raises EmbeddingGenerationError if Vertex AI fails in production mode (fail-closed).
    """
    if not texts:
        return []

    if use_vertex is None:
        use_vertex = os.getenv("USE_VERTEX_EMBEDDING", "false").lower() in ("true", "1", "yes")

    if use_vertex:
        try:
            from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput
            embedding_model = TextEmbeddingModel.from_pretrained(model_name)
            all_embeddings: list[list[float]] = []

            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                inputs = [TextEmbeddingInput(text=t, task_type=task_type) for t in batch]
                results = embedding_model.get_embeddings(inputs)
                for r in results:
                    all_embeddings.append(list(r.values))
            return all_embeddings
        except Exception as e:
            logger.error("Batch Vertex AI embedding failed in production mode (%s). Raising fail-closed exception.", e)
            raise EmbeddingGenerationError(f"Batch Vertex AI embedding failed: {e}") from e

    # Fallback batch generation for offline testing/dry-run
    return [generate_text_embedding(t, model_name=model_name, use_vertex=False) for t in texts]
