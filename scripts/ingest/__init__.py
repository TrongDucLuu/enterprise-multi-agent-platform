"""
Enterprise Knowledge Base Ingestion Package.
Provides document parsers, chunking strategies, embedding generators, and BigQuery loaders.
"""

from scripts.ingest.parsers import DocumentParser
from scripts.ingest.chunkers import (
    is_well_structured,
    chunk_by_sections,
    chunk_text,
    process_document,
)
from scripts.ingest.embedders import (
    generate_batch_embeddings,
    generate_text_embedding,
    DEFAULT_EMBEDDING_MODEL,
)
from scripts.ingest.loaders import (
    ensure_vector_index,
    check_vector_index_coverage,
    ingest_articles_to_bigquery,
    run_test_query,
)

__all__ = [
    "DocumentParser",
    "is_well_structured",
    "chunk_by_sections",
    "chunk_text",
    "process_document",
    "generate_batch_embeddings",
    "generate_text_embedding",
    "DEFAULT_EMBEDDING_MODEL",
    "ensure_vector_index",
    "check_vector_index_coverage",
    "ingest_articles_to_bigquery",
    "run_test_query",
]
