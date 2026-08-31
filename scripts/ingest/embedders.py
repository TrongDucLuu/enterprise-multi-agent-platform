"""
Embedding Generator Utilities for Enterprise Knowledge Base Ingestion.
"""

from it_helpdesk_agent.app_utils.embedding_utils import (
    DEFAULT_EMBEDDING_MODEL,
    generate_batch_embeddings,
    generate_text_embedding,
)

__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "generate_batch_embeddings",
    "generate_text_embedding",
]
