"""
Enterprise Knowledge Store package modular architecture.
Provides modular implementations for Base, In-Memory, BigQuery Vector Search,
Vertex AI Search, and L1 Facts Store.
"""
import os
from typing import Optional

from .base import (
    BaseKnowledgeStore,
    BaseFactsStore,
    KnowledgeStoreUnavailableError,
    resolve_security_context,
    resolve_retrieval_config,
    resolve_valid_system_filters,
    resolve_rerank_search_results,
    resolve_generate_text_embedding,
    resolve_authorize_document,
    resolve_is_production_mode,
    _extract_str,
    _extract_bool,
    _extract_list,
    DEFAULT_EMBEDDING_MODEL,
)
from .similarity import normalize_similarity
from .sanitize import (
    escape_xml_attribute,
    sanitize_retrieved_content,
    wrap_retrieved_document,
)
from .in_memory import (
    InMemoryKnowledgeStore,
    load_sample_articles,
    KnowledgeStore,
)
from .bigquery import BigQueryVectorKnowledgeStore
from .vertex_search import VertexAISearchKnowledgeStore
from .facts import (
    InMemoryFactsStore,
    BigQueryFactsStore,
    load_sample_facts,
)
from .query_processor import (
    preprocess_query,
    rewrite_query_with_llm,
    process_retrieval_query,
)


def get_facts_store() -> BaseFactsStore:
    is_prod = resolve_is_production_mode()
    default_backend = "bigquery" if is_prod else "in_memory"
    backend = (os.getenv("FACTS_BACKEND") or os.getenv("KNOWLEDGE_BACKEND") or default_backend).lower().strip()
    if backend == "bigquery":
        return BigQueryFactsStore()
    return InMemoryFactsStore()


def get_knowledge_store() -> BaseKnowledgeStore:
    """
    Factory to retrieve the appropriate Knowledge Store backend based on environment configuration.
    Supported backends:
      - 'in_memory' / 'mock': In-memory keyword store for local dev & unit tests.
      - 'bigquery': BigQuery serverless vector search (default in production).
      - 'vertex_ai_search' / 'discoveryengine': Google Cloud Vertex AI Search Managed Enterprise Grounding.
    """
    is_prod = resolve_is_production_mode()
    default_backend = "bigquery" if is_prod else "in_memory"
    backend = (os.getenv("KNOWLEDGE_BACKEND") or default_backend).lower().strip()
    if backend in ("vertex_ai_search", "vertex_search", "discoveryengine", "discovery_engine"):
        return VertexAISearchKnowledgeStore()
    if backend == "bigquery":
        return BigQueryVectorKnowledgeStore()
    return InMemoryKnowledgeStore()


__all__ = [
    "BaseKnowledgeStore",
    "BaseFactsStore",
    "KnowledgeStoreUnavailableError",
    "resolve_security_context",
    "resolve_retrieval_config",
    "resolve_valid_system_filters",
    "resolve_rerank_search_results",
    "resolve_generate_text_embedding",
    "resolve_authorize_document",
    "resolve_is_production_mode",
    "_extract_str",
    "_extract_bool",
    "_extract_list",
    "DEFAULT_EMBEDDING_MODEL",
    "normalize_similarity",
    "escape_xml_attribute",
    "sanitize_retrieved_content",
    "wrap_retrieved_document",
    "InMemoryKnowledgeStore",
    "load_sample_articles",
    "KnowledgeStore",
    "BigQueryVectorKnowledgeStore",
    "VertexAISearchKnowledgeStore",
    "InMemoryFactsStore",
    "BigQueryFactsStore",
    "load_sample_facts",
    "get_facts_store",
    "get_knowledge_store",
    "preprocess_query",
    "rewrite_query_with_llm",
    "process_retrieval_query",
]
