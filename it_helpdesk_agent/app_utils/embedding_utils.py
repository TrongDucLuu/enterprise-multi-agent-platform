"""
Embedding Utilities & Constants for Enterprise Knowledge Base.

Provides a unified embedding model identifier and batch embedding helper for both
the BigQuery Vector Knowledge Store and the Data Ingestion Pipeline.
"""

import os
import math
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Enterprise standard embedding model across ingestion and query
DEFAULT_EMBEDDING_MODEL = "text-embedding-005"
EMBEDDING_DIMENSIONS = 768


def generate_text_embedding(
    text: str,
    model_name: str = DEFAULT_EMBEDDING_MODEL,
    use_vertex: Optional[bool] = None
) -> list[float]:
    """
    Generates a dense vector embedding for a single text query or document chunk.
    If Vertex AI is enabled (or USE_VERTEX_EMBEDDING is true), calls Vertex AI TextEmbeddingModel.
    Otherwise, if Vertex is disabled/unavailable in local offline dev, generates a normalized fallback vector.
    """
    if use_vertex is None:
        use_vertex = os.getenv("USE_VERTEX_EMBEDDING", "true").lower() in ("true", "1", "yes")

    if use_vertex and text.strip():
        try:
            from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput
            embedding_model = TextEmbeddingModel.from_pretrained(model_name)
            inputs = [TextEmbeddingInput(text=text, task_type="RETRIEVAL_QUERY")]
            embeddings = embedding_model.get_embeddings(inputs)
            if embeddings and hasattr(embeddings[0], "values"):
                return list(embeddings[0].values)
        except Exception as e:
            logger.warning("Vertex AI embedding failed (%s), generating fallback vector.", e)

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
    task_type: str = "RETRIEVAL_DOCUMENT"
) -> list[list[float]]:
    """
    Generates embeddings for a batch of text chunks with chunking/batching support.
    """
    if not texts:
        return []

    use_vertex = os.getenv("USE_VERTEX_EMBEDDING", "true").lower() in ("true", "1", "yes")
    all_embeddings: list[list[float]] = []

    if use_vertex:
        try:
            from vertexai.language_models import TextEmbeddingModel, TextEmbeddingInput
            embedding_model = TextEmbeddingModel.from_pretrained(model_name)

            for i in range(0, len(texts), batch_size):
                batch = texts[i : i + batch_size]
                inputs = [TextEmbeddingInput(text=t, task_type=task_type) for t in batch]
                results = embedding_model.get_embeddings(inputs)
                for r in results:
                    all_embeddings.append(list(r.values))
            return all_embeddings
        except Exception as e:
            logger.warning("Batch Vertex AI embedding failed (%s), using local fallback.", e)

    # Fallback batch generation
    return [generate_text_embedding(t, model_name=model_name, use_vertex=False) for t in texts]
