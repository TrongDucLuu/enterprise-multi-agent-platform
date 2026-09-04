"""
In-memory Knowledge Store adapter for local development, rapid prototyping, and unit testing.
"""
import os
import re
import logging
from pathlib import Path
from typing import Optional, Any
import yaml

from .base import (
    BaseKnowledgeStore,
    resolve_security_context,
    resolve_valid_system_filters,
    resolve_retrieval_config,
    resolve_authorize_document,
    resolve_rerank_search_results,
)
from .similarity import normalize_similarity
from .sanitize import wrap_retrieved_document
from .query_processor import process_retrieval_query
from .corrective_retriever import (
    evaluate_retrieval_confidence,
    refine_corrective_query,
    merge_candidate_results,
)

try:
    from rag_models import (
        KnowledgeArticle,
        SearchResult,
        SectionHierarchy,
        SecurityContext,
    )
except ImportError:
    from agent_core.tools.enterprise_rag_mcp.rag_models import (
        KnowledgeArticle,
        SearchResult,
        SectionHierarchy,
        SecurityContext,
    )

logger = logging.getLogger(__name__)


def load_sample_articles(pack_name: Optional[str] = None) -> list[KnowledgeArticle]:
    """Lazy-load sample articles for local development/testing from active domain pack."""
    if pack_name is None:
        pack_name = os.getenv("DOMAIN_PACK", "it-helpdesk")

    candidate_paths = [
        Path(__file__).resolve().parent.parent.parent.parent.parent / "domain_packs" / pack_name / "sample_data" / "articles.yaml",
        Path(__file__).resolve().parent.parent.parent.parent / "domain_packs" / pack_name / "sample_data" / "articles.yaml",
        Path("domain_packs") / pack_name / "sample_data" / "articles.yaml",
    ]
    for path in candidate_paths:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and isinstance(data, list):
                    return [KnowledgeArticle(**item) for item in data]
            except Exception as e:
                logger.warning("Failed to load sample articles from %s: %s", path, e)
                return []
    return []


class InMemoryKnowledgeStore(BaseKnowledgeStore):
    """
    In-memory knowledge store supporting fast keyword-based and hybrid retrieval.
    Ideal for local development, rapid prototyping, and unit testing.
    """

    def __init__(self, articles: Optional[list[KnowledgeArticle]] = None):
        self.articles = list(articles) if articles is not None else load_sample_articles()

    def _search_candidates(
        self,
        query_text: str,
        sec_ctx: SecurityContext,
        clean_system: str,
        allowed_upper: Optional[set[str]],
        hybrid_enabled: bool,
        retrieve_k: int,
    ) -> list[SearchResult]:
        """Internal search helper to retrieve authorized candidates matching query_text."""
        # Common Vietnamese and English stop words
        STOP_WORDS = {
            "và", "các", "cho", "của", "là", "ở", "trong", "trên", "được", "với", "tại",
            "để", "khi", "có", "này", "đó", "ra", "vào", "lại", "nào", "gì", "sao",
            "làm", "như", "thế", "theo", "từ", "bị", "đã", "sẽ", "phải", "về", "hãy",
            "giúp", "tôi", "bạn", "cách", "hướng", "dẫn", "quy", "định", "bao", "nhiêu",
            "mục", "nằm", "sau", "đến", "hoặc", "một", "hai", "ba", "bốn", "năm",
            "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "is", "are"
        }

        query_lower = query_text.lower()
        raw_terms = re.findall(r'[\w\-]+', query_lower)
        terms = [t for t in raw_terms if t not in STOP_WORDS and len(t) > 1]
        if not terms:
            terms = raw_terms

        results: list[tuple[float, KnowledgeArticle]] = []

        for article in self.articles:
            # 1. Authorize document against SecurityContext (Tombstones, Expiry, Clearance, Roles)
            if not resolve_authorize_document(article, sec_ctx):
                continue

            # 2. RBAC & System filter
            art_sys = article.system.upper()
            if clean_system != "ALL" and art_sys != clean_system:
                continue
            if allowed_upper is not None and art_sys not in allowed_upper:
                continue

            score = 0.0
            art_id_lower = article.id.lower()
            if art_id_lower in query_lower:
                score += 10.0

            article_text = f"{article.title} {article.category} {article.content}".lower()
            article_keywords = [k.lower() for k in article.keywords]

            # 3. Keyword matching & Exact match boosting (M_BEST_EKO, ME21N, OB52, etc.)
            for term in terms:
                # Exact article ID match
                if term == art_id_lower:
                    score += 10.0
                # Exact phrase / code matching (case-insensitive)
                elif term in article.title.lower():
                    score += 3.0
                elif term in article_keywords:
                    score += 2.0
                elif term in article_text:
                    score += 0.5

            # Exact technical code / transaction code matching bonus
            for kw in article_keywords:
                if len(kw) >= 3 and kw in query_lower:
                    score += 4.0

            # Additional hybrid scoring bonus when hybrid search is enabled
            if hybrid_enabled:
                for term in terms:
                    if len(term) >= 4 and term in article_text:
                        score += 1.5

            # Minimum relevance threshold: require at least a meaningful keyword match
            if score >= 2.0:
                results.append((score, article))

        # Sort by relevance score descending
        results.sort(key=lambda x: x[0], reverse=True)

        search_results = []
        for score, article in results[:retrieve_k]:
            is_truncated = len(article.content) > 1200
            raw_snippet = article.content[:1200].strip() + "..." if is_truncated else article.content.strip()
            snippet = wrap_retrieved_document(
                content=raw_snippet,
                doc_id=article.id,
                system=article.system,
                title=article.title,
            )
            raw_dist = 2.0 * (1.0 - min(1.0, score / 6.0))
            relevance = normalize_similarity(raw_dist, metric="COSINE")
            sec_hier = article.section_hierarchy
            context_path = sec_hier.format_path() if sec_hier else f"{article.system} > {article.category} > {article.title}"
            search_results.append(SearchResult(
                article_id=article.id,
                parent_doc_id=article.parent_doc_id,
                chunk_index=article.chunk_index,
                system=article.system,
                title=article.title,
                snippet=snippet,
                relevance_score=relevance,
                section_h1=article.section_h1,
                section_h2=article.section_h2,
                section_h3=article.section_h3,
                section_hierarchy=sec_hier,
                context_path=context_path,
                allowed_roles=article.allowed_roles,
                sensitivity=article.sensitivity,
                clearance_level=getattr(article, "clearance_level", None),
                source_uri=article.source_uri,
                category=article.category,
                keywords=article.keywords,
                owner=article.owner,
                effective_date=article.effective_date,
                expiry_date=article.expiry_date,
                is_deleted=getattr(article, "is_deleted", False),
                is_truncated=is_truncated,
            ))
        return search_results

    def search(
        self,
        query: str,
        security_context: SecurityContext,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        """Search knowledge articles by query keywords, system filter, authorized systems, and security context."""
        valid_systems = resolve_valid_system_filters()
        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in valid_systems:
            clean_system = "ALL"

        allowed_upper = set(s.upper() for s in allowed_systems) if allowed_systems is not None else None

        # Resolve effective security context: Fail-closed (default to anonymous, never fabricate roles)
        sec_ctx = resolve_security_context(security_context=security_context)

        # Check retrieval configuration and optimize query if enabled
        retrieval_cfg = resolve_retrieval_config()
        hybrid_enabled = retrieval_cfg.get("hybrid_search_enabled", True)
        reranker_enabled = retrieval_cfg.get("reranker_enabled", False)
        corrective_enabled = retrieval_cfg.get("corrective_retrieval_enabled", False)
        max_rounds = int(retrieval_cfg.get("adaptive_retrieval_rounds", 2))
        confidence_threshold = float(retrieval_cfg.get("confidence_threshold", 0.65))

        effective_query = process_retrieval_query(query, retrieval_cfg) or query
        retrieve_k = max(limit * 4, 15) if reranker_enabled else limit

        # Round 1: Initial search
        search_results = self._search_candidates(
            query_text=effective_query,
            sec_ctx=sec_ctx,
            clean_system=clean_system,
            allowed_upper=allowed_upper,
            hybrid_enabled=hybrid_enabled,
            retrieve_k=retrieve_k,
        )

        # Corrective retrieval loop: if confidence is low, iteratively refine query and merge results
        if corrective_enabled and max_rounds > 1 and not evaluate_retrieval_confidence(search_results, threshold=confidence_threshold):
            for round_idx in range(2, max_rounds + 1):
                refined_query = refine_corrective_query(query, round_idx)
                if not refined_query or refined_query.strip() == effective_query.strip():
                    continue
                new_candidates = self._search_candidates(
                    query_text=refined_query,
                    sec_ctx=sec_ctx,
                    clean_system=clean_system,
                    allowed_upper=allowed_upper,
                    hybrid_enabled=hybrid_enabled,
                    retrieve_k=retrieve_k,
                )
                search_results = merge_candidate_results(search_results, new_candidates, limit=retrieve_k)
                if evaluate_retrieval_confidence(search_results, threshold=confidence_threshold):
                    break

        if reranker_enabled:
            search_results = resolve_rerank_search_results(
                query=effective_query,
                candidates=search_results,
                top_n=limit,
                use_reranker=True,
            )
        return search_results[:limit]

    def get_article_by_id(
        self,
        article_id: str,
        security_context: SecurityContext,
    ) -> Optional[KnowledgeArticle]:
        """Retrieves an article by its unique ID, aggregating multi-chunk documents if present, strictly authorized by security_context."""
        if not article_id:
            return None
        clean_id = article_id.upper().strip()
        
        target_parent = None
        for a in self.articles:
            if a.id.upper() == clean_id:
                if a.parent_doc_id:
                    target_parent = a.parent_doc_id.upper()
                break
            elif a.parent_doc_id and a.parent_doc_id.upper() == clean_id:
                target_parent = a.parent_doc_id.upper()
                break

        if target_parent:
            matching = [a for a in self.articles if a.parent_doc_id and a.parent_doc_id.upper() == target_parent]
        else:
            matching = [a for a in self.articles if a.id.upper() == clean_id]

        if not matching:
            return None

        if len(matching) == 1 and not matching[0].parent_doc_id:
            doc = matching[0]
        else:
            sorted_chunks = sorted(matching, key=lambda x: getattr(x, "chunk_index", 0) or 0)
            base = sorted_chunks[0]
            aggregated_content = "\n\n".join(c.content for c in sorted_chunks if c.content)
            doc = KnowledgeArticle(
                id=base.parent_doc_id or base.id,
                parent_doc_id=base.parent_doc_id,
                chunk_index=0,
                system=base.system,
                title=base.title.split(" (Phần ")[0] if " (Phần " in base.title else base.title,
                category=base.category,
                content=aggregated_content,
                keywords=base.keywords,
                section_h1=base.section_h1,
                section_h2=base.section_h2,
                section_h3=base.section_h3,
                section_hierarchy=base.section_hierarchy,
                allowed_roles=base.allowed_roles,
                sensitivity=base.sensitivity,
                clearance_level=base.clearance_level,
                source_uri=base.source_uri,
                owner=base.owner,
                effective_date=base.effective_date,
                expiry_date=base.expiry_date,
                is_deleted=base.is_deleted,
                deleted_at=base.deleted_at,
            )

        if not resolve_authorize_document(doc, security_context):
            return None
        return doc


KnowledgeStore = InMemoryKnowledgeStore
