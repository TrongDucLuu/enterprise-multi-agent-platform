"""
Backward compatibility shim for Enterprise Knowledge Store.
All core implementations have been modularized into `agent_core.tools.enterprise_rag_mcp.knowledge`.
"""
import os
import re
import math
import html
import time
import datetime
import logging
from typing import Optional, Any
from pathlib import Path
from abc import ABC, abstractmethod
import yaml

try:
    from rag_models import (
        KnowledgeArticle,
        SearchResult,
        DocumentSummary,
        SectionHierarchy,
        Fact,
        SecurityContext,
    )
except ImportError:
    from agent_core.tools.enterprise_rag_mcp.rag_models import (
        KnowledgeArticle,
        SearchResult,
        DocumentSummary,
        SectionHierarchy,
        Fact,
        SecurityContext,
    )

try:
    from agent_core.knowledge.authorize import authorize_document, resolve_doc_clearance
except ImportError:
    from knowledge.authorize import authorize_document, resolve_doc_clearance

try:
    from agent_core.app_utils.system_config import get_valid_system_filters, get_retrieval_config
    from agent_core.app_utils.embedding_utils import DEFAULT_EMBEDDING_MODEL, generate_text_embedding
    from agent_core.app_utils.reranker import rerank_search_results
    from agent_core.app_utils.env import is_production_mode
except ImportError:
    try:
        from app_utils.system_config import get_valid_system_filters, get_retrieval_config
        from app_utils.embedding_utils import DEFAULT_EMBEDDING_MODEL, generate_text_embedding
        from app_utils.reranker import rerank_search_results
        from app_utils.env import is_production_mode
    except ImportError:
        def get_valid_system_filters() -> set[str]:
            return {"ERP", "HRM", "CRM", "ALL"}
        def get_retrieval_config() -> dict[str, Any]:
            return {"fraction_lists_to_search": 0.05, "hybrid_search_enabled": False, "reranker_enabled": False}
        DEFAULT_EMBEDDING_MODEL = "text-multilingual-embedding-002"
        def generate_text_embedding(text: str, **kwargs) -> list[float]:
            return [0.0] * 768
        def rerank_search_results(query: str, candidates: list[SearchResult], **kwargs) -> list[SearchResult]:
            return candidates
        def is_production_mode() -> bool:
            return os.getenv("ENVIRONMENT", "").lower() == "production" or bool(os.getenv("K_SERVICE"))

from .knowledge import (
    BaseKnowledgeStore,
    BaseFactsStore,
    KnowledgeStoreUnavailableError,
    resolve_security_context,
    _extract_str,
    _extract_bool,
    _extract_list,
    normalize_similarity,
    escape_xml_attribute,
    sanitize_retrieved_content,
    wrap_retrieved_document,
    InMemoryKnowledgeStore,
    load_sample_articles,
    KnowledgeStore,
    BigQueryVectorKnowledgeStore,
    VertexAISearchKnowledgeStore,
    InMemoryFactsStore,
    BigQueryFactsStore,
    load_sample_facts,
    get_facts_store,
    get_knowledge_store,
    preprocess_query,
    rewrite_query_with_llm,
    process_retrieval_query,
)

__all__ = [
    "BaseKnowledgeStore",
    "BaseFactsStore",
    "KnowledgeStoreUnavailableError",
    "resolve_security_context",
    "_extract_str",
    "_extract_bool",
    "_extract_list",
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
    "KnowledgeArticle",
    "SearchResult",
    "DocumentSummary",
    "SectionHierarchy",
    "Fact",
    "SecurityContext",
    "authorize_document",
    "resolve_doc_clearance",
    "get_valid_system_filters",
    "get_retrieval_config",
    "generate_text_embedding",
    "rerank_search_results",
    "DEFAULT_EMBEDDING_MODEL",
    "is_production_mode",
]
