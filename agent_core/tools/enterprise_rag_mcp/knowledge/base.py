"""
Base interfaces, exceptions, data models, and helper functions for Enterprise Knowledge Store.
"""
import os
import re
import sys
import datetime
import logging
from abc import ABC, abstractmethod
from typing import Optional, Any

logger = logging.getLogger(__name__)


def _extract_str(val: Any) -> Optional[str]:
    """Safely extracts string representation from a BigQuery row field or model attribute."""
    if val is None:
        return None
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    return str(val)


def _extract_bool(val: Any) -> bool:
    """Safely extracts boolean value."""
    if isinstance(val, bool):
        return val
    return bool(val) if val is not None else False


def _extract_list(val: Any) -> list[str]:
    """Safely extracts list of strings from BigQuery REPEATED fields."""
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return [str(x) for x in val if x is not None]
    return [str(val)]


class KnowledgeStoreUnavailableError(Exception):
    """Raised when the primary enterprise knowledge store backend (e.g. BigQuery) fails or is unreachable."""
    pass


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
    try:
        from knowledge.authorize import authorize_document, resolve_doc_clearance
    except ImportError:
        def authorize_document(doc: Any, sec_ctx: Any) -> bool:
            return True
        def resolve_doc_clearance(doc: Any) -> int:
            return 1

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
            return {
                "fraction_lists_to_search": 0.05,
                "hybrid_search_enabled": False,
                "reranker_enabled": False,
                "query_preprocessing_enabled": False,
                "query_rewrite_enabled": False,
                "corrective_retrieval_enabled": False,
            }
        DEFAULT_EMBEDDING_MODEL = "text-multilingual-embedding-002"
        def generate_text_embedding(text: str, **kwargs) -> list[float]:
            return [0.0] * 768
        def rerank_search_results(query: str, candidates: list[Any], **kwargs) -> list[Any]:
            return candidates
        def is_production_mode() -> bool:
            return os.getenv("ENVIRONMENT", "").lower() == "production" or bool(os.getenv("K_SERVICE"))


def resolve_retrieval_config(*args, **kwargs) -> dict[str, Any]:
    ks = sys.modules.get("agent_core.tools.enterprise_rag_mcp.knowledge_store")
    if ks and hasattr(ks, "get_retrieval_config"):
        return ks.get_retrieval_config(*args, **kwargs)
    return get_retrieval_config(*args, **kwargs)


def resolve_valid_system_filters(*args, **kwargs) -> set[str]:
    ks = sys.modules.get("agent_core.tools.enterprise_rag_mcp.knowledge_store")
    if ks and hasattr(ks, "get_valid_system_filters"):
        return ks.get_valid_system_filters(*args, **kwargs)
    return get_valid_system_filters(*args, **kwargs)


def resolve_rerank_search_results(*args, **kwargs) -> list[Any]:
    ks = sys.modules.get("agent_core.tools.enterprise_rag_mcp.knowledge_store")
    if ks and hasattr(ks, "rerank_search_results"):
        return ks.rerank_search_results(*args, **kwargs)
    return rerank_search_results(*args, **kwargs)


def resolve_generate_text_embedding(*args, **kwargs) -> list[float]:
    ks = sys.modules.get("agent_core.tools.enterprise_rag_mcp.knowledge_store")
    if ks and hasattr(ks, "generate_text_embedding"):
        return ks.generate_text_embedding(*args, **kwargs)
    return generate_text_embedding(*args, **kwargs)


def resolve_authorize_document(*args, **kwargs) -> bool:
    ks = sys.modules.get("agent_core.tools.enterprise_rag_mcp.knowledge_store")
    if ks and hasattr(ks, "authorize_document"):
        return ks.authorize_document(*args, **kwargs)
    return authorize_document(*args, **kwargs)


def resolve_is_production_mode(*args, **kwargs) -> bool:
    ks = sys.modules.get("agent_core.tools.enterprise_rag_mcp.knowledge_store")
    if ks and hasattr(ks, "is_production_mode"):
        return ks.is_production_mode(*args, **kwargs)
    return is_production_mode(*args, **kwargs)


def resolve_security_context(
    security_context: Optional[SecurityContext] = None,
    user_roles: Optional[list[str]] = None,
    user_clearance: Optional[int] = None,
) -> SecurityContext:
    """
    Resolves the effective SecurityContext in an explicit, fail-closed manner.
    1. If explicit SecurityContext is passed, use it.
    2. If explicit roles/clearance are passed, construct from them.
    3. Otherwise, strictly defaults to SecurityContext.anonymous() (clearance 0, roles []).
    No ambient thread/context lookups are performed at the store layer.
    """
    if security_context is not None:
        return security_context
    if user_roles is not None or user_clearance is not None:
        return SecurityContext.from_user(roles=user_roles, clearance_level=user_clearance)
    return SecurityContext.anonymous()


class BaseKnowledgeStore(ABC):
    """Abstract Base Class for Enterprise Knowledge Stores (Adapter Pattern)."""

    @abstractmethod
    def search(
        self,
        query: str,
        security_context: SecurityContext,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        """Search knowledge articles matching the query, system filter, authorized systems, and security context."""
        pass

    @abstractmethod
    def get_article_by_id(
        self,
        article_id: str,
        security_context: SecurityContext,
    ) -> Optional[KnowledgeArticle]:
        """Retrieve the full content of an article by its unique ID, strictly authorized by security_context."""
        pass


class BaseFactsStore(ABC):
    """Abstract Base Class for Facts Knowledge Stores (L1 Kernel Registry)."""

    @abstractmethod
    def get_fact(self, key: str) -> Optional[Fact]:
        """Point-lookup of an active deterministic fact by key."""
        pass

    @abstractmethod
    def list_facts(self, domain: Optional[str] = None, status: str = "active") -> list[Fact]:
        """List active facts optionally filtered by business domain."""
        pass
