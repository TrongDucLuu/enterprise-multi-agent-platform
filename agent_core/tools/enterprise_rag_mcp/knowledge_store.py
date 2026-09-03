import os
import re
import math
import html
import time
import datetime
import logging
from pathlib import Path
import yaml
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


def escape_xml_attribute(val: Any) -> str:
    """
    Escapes special XML characters for attribute values (", <, >, &, ').
    Prevents attribute breakout and XML delimiter corruption.
    """
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def sanitize_retrieved_content(content: str) -> str:
    """
    Sanitizes raw document content to prevent delimiter injection attacks
    (e.g., embedding fake </retrieved_document> tags to break out of passive data boundary).
    Replaces any retrieved_document tag variations (case-insensitive, whitespace tolerant)
    with safe XML entity representations (&lt;...&gt;).
    """
    if not content:
        return ""
    return re.sub(
        r"<\s*(/)?\s*retrieved_document\b([^>]*)>",
        lambda m: f"&lt;{m.group(1) or ''}retrieved_document{m.group(2)}&gt;",
        content,
        flags=re.IGNORECASE
    )


def wrap_retrieved_document(content: str, doc_id: str, system: str, title: str) -> str:
    """
    Wraps retrieved document content in a secure structural XML boundary tag.
    Attributes and inner content are safely escaped to prevent delimiter and attribute injection.
    """
    safe_id = escape_xml_attribute(doc_id)
    safe_sys = escape_xml_attribute(system)
    safe_title = escape_xml_attribute(title)
    safe_content = sanitize_retrieved_content(content)
    return f'<retrieved_document id="{safe_id}" system="{safe_sys}" title="{safe_title}">\n{safe_content}\n</retrieved_document>'


try:
    from rag_models import KnowledgeArticle, SearchResult, DocumentSummary, SectionHierarchy, Fact, SecurityContext
except ImportError:
    from agent_core.tools.enterprise_rag_mcp.rag_models import KnowledgeArticle, SearchResult, DocumentSummary, SectionHierarchy, Fact, SecurityContext

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


def normalize_similarity(distance: float, metric: str = "COSINE") -> float:
    """
    Normalizes raw vector distance into a bounded similarity score in [0.0, 1.0].
    
    For COSINE distance (which ranges in [0.0, 2.0]):
        similarity = max(0.0, min(1.0, 1.0 - (distance / 2.0)))
    For EUCLIDEAN distance:
        similarity = max(0.0, min(1.0, 1.0 / (1.0 + distance)))
    For DOT_PRODUCT / other:
        similarity = max(0.0, min(1.0, (distance + 1.0) / 2.0))
        
    Rounds to 4 decimal places. Logs raw_distance and normalized_score at DEBUG level.
    """
    try:
        dist_f = float(distance)
    except (ValueError, TypeError):
        dist_f = 2.0

    metric_upper = str(metric).upper().strip() if metric else "COSINE"
    if metric_upper == "COSINE":
        score = max(0.0, min(1.0, 1.0 - (dist_f / 2.0)))
    elif metric_upper == "EUCLIDEAN":
        score = max(0.0, min(1.0, 1.0 / (1.0 + dist_f)))
    elif metric_upper == "DOT_PRODUCT":
        score = max(0.0, min(1.0, (dist_f + 1.0) / 2.0))
    else:
        score = max(0.0, min(1.0, 1.0 - (dist_f / 2.0)))

    score = round(score, 4)
    logger.debug(
        "Vector distance normalized: raw_distance=%s, metric=%s, normalized_score=%s",
        dist_f, metric_upper, score
    )
    return score


class KnowledgeStoreUnavailableError(Exception):
    """Raised when the primary enterprise knowledge store backend (e.g. BigQuery) fails or is unreachable."""
    pass


def load_sample_articles(pack_name: Optional[str] = None) -> list[KnowledgeArticle]:
    """Lazy-load sample articles for local development/testing from active domain pack."""
    if pack_name is None:
        pack_name = os.getenv("DOMAIN_PACK", "it-helpdesk")

    candidate_paths = [
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


class InMemoryKnowledgeStore(BaseKnowledgeStore):
    """
    In-memory knowledge store supporting fast keyword-based and hybrid retrieval.
    Ideal for local development, rapid prototyping, and unit testing.
    """

    def __init__(self, articles: Optional[list[KnowledgeArticle]] = None):
        self.articles = list(articles) if articles is not None else load_sample_articles()

    def search(
        self,
        query: str,
        security_context: SecurityContext,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        """Search knowledge articles by query keywords, system filter, authorized systems, and security context."""
        valid_systems = get_valid_system_filters()
        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in valid_systems:
            clean_system = "ALL"

        allowed_upper = set(s.upper() for s in allowed_systems) if allowed_systems is not None else None

        # Resolve effective security context: Fail-closed (default to anonymous, never fabricate roles)
        sec_ctx = resolve_security_context(security_context=security_context)

        # Check if hybrid search is enabled in configuration
        retrieval_cfg = get_retrieval_config()
        hybrid_enabled = retrieval_cfg.get("hybrid_search_enabled", True)

        # Common Vietnamese and English stop words
        STOP_WORDS = {
            "và", "các", "cho", "của", "là", "ở", "trong", "trên", "được", "với", "tại",
            "để", "khi", "có", "này", "đó", "ra", "vào", "lại", "nào", "gì", "sao",
            "làm", "như", "thế", "theo", "từ", "bị", "đã", "sẽ", "phải", "về", "hãy",
            "giúp", "tôi", "bạn", "cách", "hướng", "dẫn", "quy", "định", "bao", "nhiêu",
            "mục", "nằm", "sau", "đến", "hoặc", "một", "hai", "ba", "bốn", "năm",
            "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "is", "are"
        }

        query_lower = query.lower()
        raw_terms = re.findall(r'[\w\-]+', query_lower)
        terms = [t for t in raw_terms if t not in STOP_WORDS and len(t) > 1]
        if not terms:
            terms = raw_terms

        results: list[tuple[float, KnowledgeArticle]] = []

        for article in self.articles:
            # 1. Authorize document against SecurityContext (Tombstones, Expiry, Clearance, Roles)
            if not authorize_document(article, sec_ctx):
                continue

            # 2. RBAC & System filter
            art_sys = article.system.upper()
            if clean_system != "ALL" and art_sys != clean_system:
                continue
            if allowed_upper is not None and art_sys not in allowed_upper:
                continue

            score = 0.0
            article_text = f"{article.title} {article.category} {article.content}".lower()
            article_keywords = [k.lower() for k in article.keywords]

            # 3. Keyword matching & Exact match boosting (M_BEST_EKO, ME21N, OB52, etc.)
            for term in terms:
                # Exact phrase / code matching (case-insensitive)
                if term in article.title.lower():
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
        for score, article in results[:limit]:
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

        if not authorize_document(doc, security_context):
            return None
        return doc


class BigQueryVectorKnowledgeStore(BaseKnowledgeStore):
    """
    Production-grade Knowledge Store using BigQuery Vector Search and Vertex AI Embeddings.
    Fails closed when BigQuery is unreachable rather than serving mismatched mock data.
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        table_name: str = "knowledge_articles",
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        bq_client: Optional[Any] = None,
        embedding_fn: Optional[Any] = None,
    ):
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "it-helpdesk-prod")
        self.dataset_id = dataset_id or os.getenv("BIGQUERY_KB_DATASET", "it_helpdesk_kb")
        self.table_name = table_name
        self.embedding_model = embedding_model
        self.embedding_fn = embedding_fn
        self._index_active_cache: Optional[tuple[bool, float]] = None

        if bq_client is not None:
            self.bq_client = bq_client
        else:
            try:
                from google.cloud import bigquery
                self.bq_client = bigquery.Client(project=self.project_id)
            except ImportError as e:
                logger.error("google-cloud-bigquery library is required for BigQueryVectorKnowledgeStore (%s).", e)
                raise ImportError(
                    "Thư viện 'google-cloud-bigquery' chưa được cài đặt. "
                    "Hãy cài đặt google-cloud-bigquery để sử dụng backend BigQuery."
                ) from e
            except Exception as e:
                logger.error("Failed to initialize BigQuery Client for Vector Search (%s).", e)
                self.bq_client = None

    def _generate_embedding(self, text: str, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
        """Generates embedding using the shared enterprise embedding model or injected function."""
        if self.embedding_fn is not None:
            return self.embedding_fn(text)
        return generate_text_embedding(text, model_name=self.embedding_model, task_type=task_type)

    def _is_vector_index_active(self) -> bool:
        """Checks if BigQuery Vector Index exists, status is ACTIVE, and coverage >= 95.0. Caches result for 60 seconds."""
        now = time.time()
        if self._index_active_cache is not None:
            active, cached_time = self._index_active_cache
            if now - cached_time < 60:
                return active

        if not self.bq_client:
            return False

        try:
            sql = f"""
            SELECT index_status, coverage_percentage 
            FROM `{self.project_id}.{self.dataset_id}.INFORMATION_SCHEMA.VECTOR_INDEXES`
            WHERE table_name = '{self.table_name}'
            LIMIT 1
            """
            rows = list(self.bq_client.query(sql).result(timeout=5.0))
            if rows:
                status = getattr(rows[0], "index_status", "UNKNOWN")
                cov = getattr(rows[0], "coverage_percentage", None)
                is_active = (status == "ACTIVE" and cov is not None and float(cov) >= 95.0)
            else:
                is_active = False
            self._index_active_cache = (is_active, now)
            return is_active
        except Exception as e:
            logger.warning("Could not verify vector index coverage (%s), failing closed (assume inactive).", e)
            return False

    def search(
        self,
        query: str,
        security_context: SecurityContext,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        """
        Searches BigQuery table using VECTOR_SEARCH with Pre-filtering subquery and scalar clearance level pre-filter.
        Fails closed by raising KnowledgeStoreUnavailableError on backend failure.
        """
        if not self.bq_client:
            logger.error("BigQuery client is not initialized. Raising KnowledgeStoreUnavailableError.")
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Knowledge Store chưa được khởi tạo.")

        valid_systems = get_valid_system_filters()
        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in valid_systems:
            clean_system = "ALL"

        # Resolve effective security context: Fail-closed (default to anonymous, never fabricate roles)
        sec_ctx = resolve_security_context(security_context=security_context)

        # 2. Get retrieval configuration (fraction_lists_to_search, hybrid_search_enabled, retrieve_k, final_k, adaptive_retrieval_rounds)
        retrieval_cfg = get_retrieval_config()
        fraction_lists_to_search = retrieval_cfg.get("fraction_lists_to_search", 0.05)
        hybrid_enabled = retrieval_cfg.get("hybrid_search_enabled", True)
        reranker_enabled = retrieval_cfg.get("reranker_enabled", False) or os.getenv("USE_VERTEX_RERANKER", "false").lower() in ("true", "1", "yes")

        cfg_retrieve_k = retrieval_cfg.get("retrieve_k", 20)
        cfg_final_k = retrieval_cfg.get("final_k", 3)
        target_final_k = limit if limit is not None and limit > 0 else cfg_final_k
        max_rounds = int(retrieval_cfg.get("adaptive_retrieval_rounds", 2))

        try:
            query_vec = self._generate_embedding(query)
            full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"

            from google.cloud import bigquery
            today_iso = datetime.date.today().isoformat()

            # Base scalar pre-filters (tombstone + dates + scalar clearance level)
            base_filters = (
                "(is_deleted IS NOT TRUE) "
                "AND (expiry_date IS NULL OR expiry_date >= @today) "
                "AND (effective_date IS NULL OR effective_date <= @today) "
                "AND (clearance_level IS NULL OR clearance_level <= @user_clearance)"
            )

            index_active = self._is_vector_index_active()
            options_clause = f", options => '{{\"fraction_lists_to_search\": {fraction_lists_to_search}}}'" if index_active else ""

            authorized_candidates: list[SearchResult] = []
            seen_ids: set[str] = set()
            current_retrieve_k = max(cfg_retrieve_k, target_final_k * 2, 10)

            for round_idx in range(1, max_rounds + 1):
                query_params = [
                    bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vec),
                    bigquery.ScalarQueryParameter("limit", "INT64", current_retrieve_k),
                    bigquery.ScalarQueryParameter("today", "DATE", today_iso),
                    bigquery.ScalarQueryParameter("user_clearance", "INT64", sec_ctx.clearance_level),
                ]

                # 1. Construct Pre-Filter Subquery for VECTOR_SEARCH (Tombstone + Dates + System RBAC + Scalar Clearance)
                if clean_system != "ALL":
                    base_table_expr = f"(SELECT * FROM {full_table} WHERE system = @system_param AND {base_filters})"
                    query_params.append(bigquery.ScalarQueryParameter("system_param", "STRING", clean_system))
                elif allowed_systems is not None:
                    clean_allowed = [s.upper() for s in allowed_systems if s.upper() in valid_systems and s.upper() != "ALL"]
                    if not clean_allowed:
                        return []
                    base_table_expr = f"(SELECT * FROM {full_table} WHERE system IN UNNEST(@allowed_systems_param) AND {base_filters})"
                    query_params.append(bigquery.ArrayQueryParameter("allowed_systems_param", "STRING", clean_allowed))
                else:
                    base_table_expr = f"(SELECT * FROM {full_table} WHERE {base_filters})"

                if hybrid_enabled:
                    query_params.append(bigquery.ScalarQueryParameter("query_text", "STRING", query.strip()))
                    sql = f"""
                    SELECT 
                        base.id, 
                        base.parent_doc_id,
                        base.chunk_index,
                        base.system, 
                        base.title, 
                        base.content, 
                        base.section_h1,
                        base.section_h2,
                        base.section_h3,
                        base.allowed_roles,
                        base.sensitivity,
                        base.clearance_level,
                        base.source_uri,
                        base.category,
                        base.keywords,
                        base.owner,
                        base.effective_date,
                        base.expiry_date,
                        base.is_deleted,
                        distance
                    FROM VECTOR_SEARCH(
                        {base_table_expr},
                        'embedding',
                        query_value => @query_vector,
                        lexical_search_columns => ['title', 'content', 'keywords'],
                        lexical_search_query_value => @query_text,
                        top_k => @limit,
                        distance_type => 'COSINE'{options_clause}
                    )
                    """
                else:
                    # Pure Vector Search SQL with BigQuery VECTOR_SEARCH Pre-Filtering & Stored Fields
                    sql = f"""
                    SELECT 
                        base.id, 
                        base.parent_doc_id,
                        base.chunk_index,
                        base.system, 
                        base.title, 
                        base.content, 
                        base.section_h1,
                        base.section_h2,
                        base.section_h3,
                        base.allowed_roles,
                        base.sensitivity,
                        base.clearance_level,
                        base.source_uri,
                        base.category,
                        base.keywords,
                        base.owner,
                        base.effective_date,
                        base.expiry_date,
                        base.is_deleted,
                        distance
                    FROM VECTOR_SEARCH(
                        {base_table_expr},
                        'embedding',
                        query_value => @query_vector,
                        top_k => @limit,
                        distance_type => 'COSINE'{options_clause}
                    )
                    ORDER BY distance ASC
                    """

                bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "3.0"))
                job_timeout_ms = int(bq_timeout * 1000)
                job_config = bigquery.QueryJobConfig(
                    query_parameters=query_params,
                    job_timeout_ms=job_timeout_ms,
                )
                query_job = self.bq_client.query(sql, job_config=job_config)
                try:
                    rows = list(query_job.result(timeout=bq_timeout))
                except Exception as query_err:
                    try:
                        query_job.cancel()
                    except Exception as cancel_err:
                        logger.debug("Failed to cancel BigQuery query job: %s", cancel_err)
                    raise query_err

                # Telemetry: Record and log BigQuery query resource consumption
                try:
                    bytes_billed = getattr(query_job, "total_bytes_billed", None)
                    bytes_processed = getattr(query_job, "total_bytes_processed", None)
                    cache_hit = getattr(query_job, "cache_hit", None)
                    slot_ms = getattr(query_job, "slot_millis", None)
                    logger.info(
                        "BigQuery Vector Search Telemetry (round %d/%d, retrieve_k=%d): bytes_billed=%s, bytes_processed=%s, cache_hit=%s, slot_ms=%s, job_id=%s",
                        round_idx, max_rounds, current_retrieve_k, bytes_billed, bytes_processed, cache_hit, slot_ms, getattr(query_job, "job_id", None)
                    )
                except Exception as telem_err:
                    logger.debug("Error recording BigQuery telemetry: %s", telem_err)

                for row in rows:
                    art_id = _extract_str(getattr(row, "id", "")) or ""
                    if not art_id or art_id in seen_ids:
                        continue

                    content_raw = getattr(row, "content", "")
                    content_str = str(content_raw) if content_raw is not None else ""
                    
                    dist_val = getattr(row, "distance", 0.0)
                    relevance = normalize_similarity(dist_val, metric="COSINE")

                    sec_h1 = _extract_str(getattr(row, "section_h1", None))
                    sec_h2 = _extract_str(getattr(row, "section_h2", None))
                    sec_h3 = _extract_str(getattr(row, "section_h3", None))
                    
                    raw_hier = getattr(row, "section_hierarchy", None)
                    if raw_hier and not any([sec_h1, sec_h2, sec_h3]):
                        hier_dict = dict(raw_hier) if hasattr(raw_hier, "items") else (raw_hier if isinstance(raw_hier, dict) else {})
                        sec_h1 = _extract_str(hier_dict.get("h1"))
                        sec_h2 = _extract_str(hier_dict.get("h2"))
                        sec_h3 = _extract_str(hier_dict.get("h3"))

                    sec_hier = None
                    if any([sec_h1, sec_h2, sec_h3]):
                        sec_hier = SectionHierarchy(h1=sec_h1, h2=sec_h2, h3=sec_h3)

                    art_sys = _extract_str(getattr(row, "system", "")) or ""
                    art_title = _extract_str(getattr(row, "title", "")) or ""
                    category = _extract_str(getattr(row, "category", None))
                    
                    context_path = sec_hier.format_path() if sec_hier else f"{art_sys} > {category or 'General'} > {art_title}"
                    
                    is_truncated = len(content_str) > 1200
                    raw_snippet = content_str[:1200].strip() + "..." if is_truncated else content_str.strip()
                    snippet = wrap_retrieved_document(
                        content=raw_snippet,
                        doc_id=art_id,
                        system=art_sys,
                        title=art_title,
                    )

                    candidate = SearchResult(
                        article_id=art_id,
                        parent_doc_id=_extract_str(getattr(row, "parent_doc_id", None)),
                        chunk_index=getattr(row, "chunk_index", None) if isinstance(getattr(row, "chunk_index", None), int) else None,
                        system=art_sys,
                        title=art_title,
                        snippet=snippet,
                        relevance_score=relevance,
                        section_h1=sec_h1,
                        section_h2=sec_h2,
                        section_h3=sec_h3,
                        section_hierarchy=sec_hier,
                        context_path=context_path,
                        allowed_roles=_extract_list(getattr(row, "allowed_roles", None)),
                        sensitivity=_extract_str(getattr(row, "sensitivity", None)),
                        clearance_level=getattr(row, "clearance_level", None),
                        source_uri=_extract_str(getattr(row, "source_uri", None)),
                        category=category,
                        keywords=_extract_list(getattr(row, "keywords", None)),
                        owner=_extract_str(getattr(row, "owner", None)),
                        effective_date=_extract_str(getattr(row, "effective_date", None)),
                        expiry_date=_extract_str(getattr(row, "expiry_date", None)),
                        is_deleted=_extract_bool(getattr(row, "is_deleted", False)),
                        is_truncated=is_truncated,
                    )

                    # Authorize candidate BEFORE adding to candidate pool for reranking
                    if not authorize_document(candidate, sec_ctx):
                        continue

                    seen_ids.add(art_id)
                    authorized_candidates.append(candidate)

                # Check if we have enough authorized candidates or exhausted rounds / database rows
                if len(authorized_candidates) >= target_final_k or round_idx >= max_rounds or len(rows) < current_retrieve_k:
                    break

                # Adaptively increase retrieve limit for subsequent round
                current_retrieve_k = max(current_retrieve_k * 2, target_final_k * 4)

            # Rerank strictly AFTER authorization: Unauthorized docs are NEVER sent to the reranker
            if reranker_enabled:
                authorized_candidates = rerank_search_results(
                    query=query,
                    candidates=authorized_candidates,
                    top_n=target_final_k,
                    project_id=self.project_id,
                    ranking_model=retrieval_cfg.get("reranker_model", "semantic-ranker-512@latest"),
                    use_reranker=True,
                )
            return authorized_candidates[:target_final_k]
        except Exception as e:
            logger.error("BigQuery vector search failed (%s). Raising KnowledgeStoreUnavailableError.", e)
            raise KnowledgeStoreUnavailableError(f"Truy vấn BigQuery Vector Search thất bại hoặc quá thời gian chờ: {e}") from e

    def get_article_by_id(
        self,
        article_id: str,
        security_context: SecurityContext,
    ) -> Optional[KnowledgeArticle]:
        """Retrieves article by ID from BigQuery table, aggregating multi-chunk documents if present, strictly authorized by security_context."""
        if not self.bq_client:
            logger.error("BigQuery client is not initialized for get_article_by_id.")
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Knowledge Store chưa được khởi tạo.")

        if not article_id:
            return None
        clean_id = article_id.upper().strip()
        full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"
        today_iso = datetime.date.today().isoformat()
        sql = f"""
        SELECT 
            id, parent_doc_id, chunk_index, system, title, category, content, keywords,
            section_h1, section_h2, section_h3, allowed_roles, sensitivity, clearance_level,
            source_uri, owner, effective_date, expiry_date, is_deleted, deleted_at 
        FROM {full_table} 
        WHERE (is_deleted IS NOT TRUE)
          AND (expiry_date IS NULL OR expiry_date >= @today)
          AND (effective_date IS NULL OR effective_date <= @today)
          AND (
               UPPER(id) = @article_id 
            OR UPPER(parent_doc_id) = @article_id 
            OR UPPER(parent_doc_id) = (
                SELECT UPPER(parent_doc_id) FROM {full_table} WHERE UPPER(id) = @article_id AND parent_doc_id IS NOT NULL LIMIT 1
            )
          )
        ORDER BY chunk_index ASC
        """
        try:
            bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "3.0"))
            from google.cloud import bigquery
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("article_id", "STRING", clean_id),
                    bigquery.ScalarQueryParameter("today", "DATE", today_iso),
                ],
                job_timeout_ms=int(bq_timeout * 1000),
            )
            query_job = self.bq_client.query(sql, job_config=job_config)
            try:
                rows = list(query_job.result(timeout=bq_timeout))
            except Exception as q_err:
                try:
                    query_job.cancel()
                except Exception as c_err:
                    logger.debug("Failed to cancel BigQuery get_article_by_id query job: %s", c_err)
                raise q_err
            if not rows:
                return None

            r = rows[0]
            sec_h1 = _extract_str(getattr(r, "section_h1", None))
            sec_h2 = _extract_str(getattr(r, "section_h2", None))
            sec_h3 = _extract_str(getattr(r, "section_h3", None))
            sec_hier = None
            if any([sec_h1, sec_h2, sec_h3]):
                sec_hier = SectionHierarchy(h1=sec_h1, h2=sec_h2, h3=sec_h3)

            if len(rows) > 1:
                combined_content = "\n\n".join(
                    _extract_str(getattr(row, "content", "")) or "" 
                    for row in sorted(rows, key=lambda x: getattr(x, "chunk_index", 0) or 0)
                )
            else:
                combined_content = _extract_str(getattr(r, "content", "")) or ""

            article = KnowledgeArticle(
                id=_extract_str(getattr(r, "parent_doc_id", None)) or _extract_str(getattr(r, "id", "")) or "",
                parent_doc_id=_extract_str(getattr(r, "parent_doc_id", None)),
                chunk_index=0 if len(rows) > 1 else (getattr(r, "chunk_index", None) if isinstance(getattr(r, "chunk_index", None), int) else None),
                system=_extract_str(getattr(r, "system", "")) or "",
                title=_extract_str(getattr(r, "title", "")) or "",
                category=_extract_str(getattr(r, "category", None)) or "General",
                content=combined_content,
                keywords=_extract_list(getattr(r, "keywords", None)),
                section_h1=sec_h1,
                section_h2=sec_h2,
                section_h3=sec_h3,
                section_hierarchy=sec_hier,
                allowed_roles=_extract_list(getattr(r, "allowed_roles", None)),
                sensitivity=_extract_str(getattr(r, "sensitivity", None)),
                clearance_level=getattr(r, "clearance_level", None),
                source_uri=_extract_str(getattr(r, "source_uri", None)),
                owner=_extract_str(getattr(r, "owner", None)),
                effective_date=_extract_str(getattr(r, "effective_date", None)),
                expiry_date=_extract_str(getattr(r, "expiry_date", None)),
                is_deleted=_extract_bool(getattr(r, "is_deleted", False)),
                deleted_at=_extract_str(getattr(r, "deleted_at", None)),
            )
            if not authorize_document(article, security_context):
                return None
            return article
        except Exception as e:
            logger.error("BigQuery get_article_by_id failed (%s). Raising KnowledgeStoreUnavailableError.", e)
            raise KnowledgeStoreUnavailableError(f"Truy xuất bài viết BigQuery thất bại: {e}") from e


class VertexAISearchKnowledgeStore(BaseKnowledgeStore):
    """
    Google Cloud Discovery Engine / Vertex AI Search Knowledge Store Adapter.
    Leverages Google's Managed Enterprise Grounding, Multi-modal OCR, Semantic Search,
    Extractive Segments, and Citation Attribution.
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        location: Optional[str] = None,
        data_store_id: Optional[str] = None,
        serving_config_id: Optional[str] = None,
        collection_id: Optional[str] = None,
        search_client: Optional[Any] = None,
        timeout: Optional[float] = None,
    ):
        self.project_id = (
            project_id
            or os.getenv("VERTEX_SEARCH_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT")
            or "test-project"
        )
        self.location = (
            location
            or os.getenv("VERTEX_SEARCH_LOCATION")
            or os.getenv("GOOGLE_CLOUD_LOCATION")
            or "global"
        )
        self.data_store_id = (
            data_store_id
            or os.getenv("VERTEX_SEARCH_DATA_STORE_ID")
            or os.getenv("DATA_STORE_ID")
            or "enterprise-knowledge-store"
        )
        self.serving_config_id = (
            serving_config_id
            or os.getenv("VERTEX_SEARCH_SERVING_CONFIG_ID")
            or "default_search"
        )
        self.collection_id = (
            collection_id
            or os.getenv("VERTEX_SEARCH_COLLECTION_ID")
            or "default_collection"
        )
        self.timeout = timeout or float(os.getenv("VERTEX_SEARCH_TIMEOUT_SECONDS", "5.0"))
        self._search_client = search_client

    @property
    def client(self) -> Any:
        if self._search_client is None:
            try:
                from google.cloud import discoveryengine_v1 as discoveryengine
                self._search_client = discoveryengine.SearchServiceClient()
            except Exception as e:
                logger.error("Failed to initialize Vertex AI Search / Discovery Engine client: %s", e)
                raise KnowledgeStoreUnavailableError(
                    f"Không thể khởi tạo kết nối Vertex AI Search Discovery Engine: {e}"
                ) from e
        return self._search_client

    def _get_serving_config_path(self) -> str:
        """Constructs the fully-qualified resource name for the serving config."""
        return (
            f"projects/{self.project_id}/locations/{self.location}/collections/"
            f"{self.collection_id}/dataStores/{self.data_store_id}/servingConfigs/{self.serving_config_id}"
        )

    def search(
        self,
        query: str,
        security_context: SecurityContext,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        if not query or not query.strip():
            return []

        clean_sys = system.upper().strip() if system else "ALL"

        # Resolve effective security context: Fail-closed (default to anonymous, never fabricate roles)
        sec_ctx = resolve_security_context(security_context=security_context)

        # Build filter expression
        filter_parts = []
        if clean_sys != "ALL":
            filter_parts.append(f'system: ANY("{clean_sys}")')
        elif allowed_systems:
            allowed_clause = ", ".join(f'"{s.upper().strip()}"' for s in allowed_systems if s)
            if allowed_clause:
                filter_parts.append(f"system: ANY({allowed_clause})")

        filter_expr = " AND ".join(filter_parts) if filter_parts else None
        serving_config = self._get_serving_config_path()

        try:
            from google.cloud import discoveryengine_v1 as discoveryengine
            request = discoveryengine.SearchRequest(
                serving_config=serving_config,
                query=query.strip(),
                page_size=limit,
                filter=filter_expr,
                content_search_spec=discoveryengine.SearchRequest.ContentSearchSpec(
                    snippet_spec=discoveryengine.SearchRequest.ContentSearchSpec.SnippetSpec(
                        return_snippet=True
                    ),
                    summary_spec=discoveryengine.SearchRequest.ContentSearchSpec.SummarySpec(
                        summary_result_count=limit,
                        include_citations=True,
                    ),
                    extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
                        max_extractive_answer_count=1,
                        max_extractive_segment_count=1,
                    ),
                ),
            )
            response = self.client.search(request=request, timeout=self.timeout)
        except KnowledgeStoreUnavailableError:
            raise
        except Exception as e:
            logger.error("Vertex AI Search query failed for query '%s': %s", query, e)
            raise KnowledgeStoreUnavailableError(
                f"Truy vấn Vertex AI Search Datastore thất bại hoặc quá thời gian chờ: {e}"
            ) from e

        search_results: list[SearchResult] = []
        raw_results = getattr(response, "results", []) or []
        for item in raw_results:
            doc = getattr(item, "document", None)
            if not doc:
                continue

            doc_data = {}
            if hasattr(doc, "struct_data") and doc.struct_data:
                doc_data = dict(doc.struct_data)
            elif hasattr(doc, "json_data") and doc.json_data:
                import json
                try:
                    doc_data = json.loads(doc.json_data)
                except Exception:
                    doc_data = {}

            doc_id = (
                doc_data.get("id")
                or doc_data.get("article_id")
                or getattr(doc, "id", None)
                or getattr(doc, "name", "DOC-UNKNOWN").split("/")[-1]
            )
            title = (
                doc_data.get("title")
                or getattr(doc, "title", None)
                or doc_data.get("name")
                or doc_id
            )
            doc_sys = doc_data.get("system") or clean_sys

            # Extract snippet / extractive answer / extractive segment
            snippet_text = ""
            derived = getattr(doc, "derived_struct_data", {}) or {}
            extractive_answers = derived.get("extractive_answers", [])
            extractive_segments = derived.get("extractive_segments", [])
            snippets = derived.get("snippets", [])

            if extractive_answers and isinstance(extractive_answers, list) and len(extractive_answers) > 0:
                snippet_text = extractive_answers[0].get("content", "")
            elif extractive_segments and isinstance(extractive_segments, list) and len(extractive_segments) > 0:
                snippet_text = extractive_segments[0].get("content", "")
            elif snippets and isinstance(snippets, list) and len(snippets) > 0:
                snippet_text = snippets[0].get("snippet", "")

            if not snippet_text:
                snippet_text = doc_data.get("content") or doc_data.get("snippet") or ""

            # Score extraction
            relevance = 1.0
            if hasattr(item, "model_scores") and item.model_scores:
                relevance = float(item.model_scores.get("relevance", 1.0))
            elif hasattr(item, "relevance_score") and item.relevance_score is not None:
                relevance = float(item.relevance_score)

            # Section hierarchy
            h1 = doc_data.get("section_h1")
            h2 = doc_data.get("section_h2")
            h3 = doc_data.get("section_h3")
            sec_hier = None
            if h1 or h2 or h3:
                sec_hier = SectionHierarchy(h1=h1, h2=h2, h3=h3)
            context_path = " > ".join(filter(None, [h1, h2, h3])) or None

            wrapped_snippet = wrap_retrieved_document(
                content=snippet_text,
                doc_id=doc_id,
                system=doc_sys,
                title=title
            )

            res = SearchResult(
                article_id=doc_id,
                title=title,
                snippet=wrapped_snippet,
                system=doc_sys,
                category=doc_data.get("category", "General"),
                relevance_score=round(relevance, 4),
                section_h1=h1,
                section_h2=h2,
                section_h3=h3,
                section_hierarchy=sec_hier,
                context_path=context_path,
                chunk_index=doc_data.get("chunk_index"),
                parent_doc_id=doc_data.get("parent_doc_id"),
                allowed_roles=_extract_list(doc_data.get("allowed_roles")),
                sensitivity=_extract_str(doc_data.get("sensitivity", "INTERNAL")),
                clearance_level=doc_data.get("clearance_level"),
                source_uri=_extract_str(doc_data.get("source_uri")),
                keywords=_extract_list(doc_data.get("keywords")),
                owner=doc_data.get("owner"),
                effective_date=_extract_str(doc_data.get("effective_date")),
                expiry_date=_extract_str(doc_data.get("expiry_date")),
                is_deleted=_extract_bool(doc_data.get("is_deleted")),
                is_truncated=len(snippet_text) > 1200,
            )

            if not authorize_document(res, sec_ctx):
                continue

            search_results.append(res)

        # Apply reranker if enabled
        retrieval_cfg = get_retrieval_config()
        reranker_enabled = retrieval_cfg.get("reranker_enabled", False) or os.getenv("USE_VERTEX_RERANKER", "false").lower() in ("true", "1", "yes")
        if reranker_enabled and search_results:
            search_results = rerank_search_results(
                query=query,
                candidates=search_results,
                top_n=limit,
                project_id=self.project_id,
                ranking_model=retrieval_cfg.get("reranker_model", "semantic-ranker-512@latest"),
                use_reranker=True,
            )

        return search_results[:limit]

    def get_article_by_id(
        self,
        article_id: str,
        security_context: SecurityContext,
    ) -> Optional[KnowledgeArticle]:
        """Retrieves article by ID from Vertex AI Search datastore, strictly authorized by security_context."""
        if not article_id:
            return None
        clean_id = article_id.strip()

        try:
            results = self.search(query=clean_id, security_context=security_context, limit=5)
            for r in results:
                if r.article_id.lower() == clean_id.lower():
                    article = KnowledgeArticle(
                        id=r.article_id,
                        system=r.system,
                        title=r.title,
                        category=r.category,
                        content=r.snippet,
                        allowed_roles=r.allowed_roles,
                        sensitivity=r.sensitivity,
                        clearance_level=r.clearance_level,
                        section_hierarchy=r.section_hierarchy,
                        owner=r.owner,
                        effective_date=r.effective_date,
                        expiry_date=r.expiry_date,
                        is_deleted=r.is_deleted,
                    )
                    if not authorize_document(article, security_context):
                        return None
                    return article
            return None
        except KnowledgeStoreUnavailableError:
            raise
        except Exception as e:
            logger.error("Vertex AI Search get_article_by_id failed for '%s': %s", clean_id, e)
            raise KnowledgeStoreUnavailableError(
                f"Không thể truy xuất bài viết từ Vertex AI Search: {e}"
            ) from e



def load_sample_facts(pack_name: Optional[str] = None) -> list[Fact]:
    """Lazy-load sample L1 facts for local development/testing from active domain pack."""
    if pack_name is None:
        pack_name = os.getenv("DOMAIN_PACK", "it-helpdesk")

    candidate_paths = [
        Path(__file__).resolve().parent.parent.parent.parent / "domain_packs" / pack_name / "sample_data" / "facts.yaml",
        Path("domain_packs") / pack_name / "sample_data" / "facts.yaml",
    ]
    for path in candidate_paths:
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                if data and isinstance(data, list):
                    return [Fact(**item) for item in data]
            except Exception as e:
                logger.warning("Failed to load sample facts from %s: %s", path, e)
                return []
    return []



class BaseFactsStore(ABC):
    """Abstract Base Class for Facts Knowledge Stores (L1 Kernel Registry)."""

    @abstractmethod
    def get_fact(self, key: str) -> Optional[Fact]:
        """Point-lookup an active fact by exact unique key."""
        pass

    @abstractmethod
    def list_facts(self, domain: Optional[str] = None, status: str = "active") -> list[Fact]:
        """Lists facts matching optional domain and status filter."""
        pass


class InMemoryFactsStore(BaseFactsStore):
    """In-memory facts store for fast deterministic point-lookups (local dev & testing)."""

    def __init__(self, facts: Optional[list[Fact]] = None):
        self.facts = list(facts) if facts is not None else load_sample_facts()

    def get_fact(self, key: str) -> Optional[Fact]:
        if not key:
            return None
        clean_k = key.strip().lower()
        for f in self.facts:
            if f.key.lower() == clean_k and f.status == "active":
                return f
        return None

    def list_facts(self, domain: Optional[str] = None, status: str = "active") -> list[Fact]:
        clean_dom = domain.strip().upper() if domain else None
        clean_status = status.strip().lower() if status else None
        res = []
        for f in self.facts:
            if clean_dom and f.domain.upper() != clean_dom:
                continue
            if clean_status and f.status.lower() != clean_status:
                continue
            res.append(f)
        return res


class BigQueryFactsStore(BaseFactsStore):
    """BigQuery backend for L1 deterministic facts point-lookup."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        table_name: Optional[str] = None,
    ):
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "test-project")
        self.dataset_id = dataset_id or os.getenv("BIGQUERY_DATASET", "it_helpdesk_kb")
        self.table_name = table_name or os.getenv("FACTS_TABLE_NAME", "l1_facts")
        self.bq_client = None

        if os.getenv("KNOWLEDGE_BACKEND", "").lower() == "bigquery" or os.getenv("FACTS_BACKEND", "").lower() == "bigquery":
            try:
                from google.cloud import bigquery
                self.bq_client = bigquery.Client(project=self.project_id)
            except Exception as e:
                logger.error("Failed to initialize BigQuery client for facts store: %s", e)
                raise KnowledgeStoreUnavailableError(f"Không thể khởi tạo kết nối BigQuery Facts Store: {e}") from e

    def get_fact(self, key: str) -> Optional[Fact]:
        if not self.bq_client:
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Facts Store chưa được khởi tạo.")
        if not key:
            return None

        clean_key = key.strip()
        full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"
        sql = f"""
        SELECT 
            fact_id, domain, key, value, value_type, unit, source_document,
            date_updated, updated_by, status, superseded_by, notes,
            clearance_level, allowed_roles
        FROM {full_table}
        WHERE LOWER(key) = LOWER(@key) AND status = 'active'
        LIMIT 1
        """
        try:
            bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "3.0"))
            from google.cloud import bigquery
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("key", "STRING", clean_key)
                ],
                job_timeout_ms=int(bq_timeout * 1000),
            )
            query_job = self.bq_client.query(sql, job_config=job_config)
            try:
                rows = list(query_job.result(timeout=bq_timeout))
            except Exception as q_err:
                try:
                    query_job.cancel()
                except Exception as c_err:
                    logger.debug("Failed to cancel BigQuery get_fact query job: %s", c_err)
                raise q_err

            if not rows:
                return None

            r = rows[0]
            return Fact(
                fact_id=_extract_str(getattr(r, "fact_id", "")),
                domain=_extract_str(getattr(r, "domain", "")),
                key=_extract_str(getattr(r, "key", "")),
                value=_extract_str(getattr(r, "value", "")),
                value_type=_extract_str(getattr(r, "value_type", "string")),
                unit=_extract_str(getattr(r, "unit", None)),
                source_document=_extract_str(getattr(r, "source_document", None)),
                date_updated=_extract_str(getattr(r, "date_updated", "")),
                updated_by=_extract_str(getattr(r, "updated_by", "human")),
                status=_extract_str(getattr(r, "status", "active")),
                superseded_by=_extract_str(getattr(r, "superseded_by", None)),
                notes=_extract_str(getattr(r, "notes", None)),
                clearance_level=getattr(r, "clearance_level", 1) if getattr(r, "clearance_level", None) is not None else 1,
                allowed_roles=_extract_list(getattr(r, "allowed_roles", None)),
            )
        except Exception as e:
            logger.error("BigQuery get_fact failed: %s", e)
            raise KnowledgeStoreUnavailableError(f"Truy vấn BigQuery Facts Store thất bại: {e}") from e

    def list_facts(self, domain: Optional[str] = None, status: str = "active") -> list[Fact]:
        if not self.bq_client:
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Facts Store chưa được khởi tạo.")
        full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"
        where_clauses = ["status = @status"]
        from google.cloud import bigquery
        params = [bigquery.ScalarQueryParameter("status", "STRING", status)]
        if domain:
            where_clauses.append("UPPER(domain) = UPPER(@domain)")
            params.append(bigquery.ScalarQueryParameter("domain", "STRING", domain))

        sql = f"""
        SELECT 
            fact_id, domain, key, value, value_type, unit, source_document,
            date_updated, updated_by, status, superseded_by, notes,
            clearance_level, allowed_roles
        FROM {full_table}
        WHERE {" AND ".join(where_clauses)}
        """
        try:
            bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "3.0"))
            job_config = bigquery.QueryJobConfig(
                query_parameters=params,
                job_timeout_ms=int(bq_timeout * 1000),
            )
            query_job = self.bq_client.query(sql, job_config=job_config)
            rows = list(query_job.result(timeout=bq_timeout))
            return [
                Fact(
                    fact_id=_extract_str(getattr(r, "fact_id", "")),
                    domain=_extract_str(getattr(r, "domain", "")),
                    key=_extract_str(getattr(r, "key", "")),
                    value=_extract_str(getattr(r, "value", "")),
                    value_type=_extract_str(getattr(r, "value_type", "string")),
                    unit=_extract_str(getattr(r, "unit", None)),
                    source_document=_extract_str(getattr(r, "source_document", None)),
                    date_updated=_extract_str(getattr(r, "date_updated", "")),
                    updated_by=_extract_str(getattr(r, "updated_by", "human")),
                    status=_extract_str(getattr(r, "status", "active")),
                    superseded_by=_extract_str(getattr(r, "superseded_by", None)),
                    notes=_extract_str(getattr(r, "notes", None)),
                    clearance_level=getattr(r, "clearance_level", 1) if getattr(r, "clearance_level", None) is not None else 1,
                    allowed_roles=_extract_list(getattr(r, "allowed_roles", None)),
                )
                for r in rows
            ]
        except Exception as e:
            logger.error("BigQuery list_facts failed: %s", e)
            raise KnowledgeStoreUnavailableError(f"Truy vấn BigQuery list_facts thất bại: {e}") from e


def get_facts_store() -> BaseFactsStore:
    is_prod = is_production_mode()
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
    is_prod = is_production_mode()
    default_backend = "bigquery" if is_prod else "in_memory"
    backend = (os.getenv("KNOWLEDGE_BACKEND") or default_backend).lower().strip()
    if backend in ("vertex_ai_search", "vertex_search", "discoveryengine", "discovery_engine"):
        return VertexAISearchKnowledgeStore()
    if backend == "bigquery":
        return BigQueryVectorKnowledgeStore()
    return InMemoryKnowledgeStore()


# Backward compatibility alias
KnowledgeStore = InMemoryKnowledgeStore


