"""
Embedding Generator Utilities for Enterprise Knowledge Base Ingestion.
"""

from agent_core.app_utils.embedding_utils import (
    DEFAULT_EMBEDDING_MODEL,
    generate_batch_embeddings,
    generate_text_embedding,
)

EMBEDDING_MODEL = DEFAULT_EMBEDDING_MODEL
EMBEDDING_DIM = 768

__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "generate_batch_embeddings",
    "generate_text_embedding",
]
