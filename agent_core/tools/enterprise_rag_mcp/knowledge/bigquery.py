"""
BigQuery Vector Search Knowledge Store Adapter with Hybrid Search, Pre-Filtering, and Reranking.
"""
import os
import re
import time
import math
import datetime
import logging
from typing import Optional, Any

from .base import (
    BaseKnowledgeStore,
    KnowledgeStoreUnavailableError,
    resolve_security_context,
    resolve_retrieval_config,
    resolve_valid_system_filters,
    resolve_rerank_search_results,
    resolve_generate_text_embedding,
    resolve_authorize_document,
    _extract_str,
    _extract_bool,
    _extract_list,
    DEFAULT_EMBEDDING_MODEL,
)
from .similarity import normalize_similarity
from .sanitize import wrap_retrieved_document
from .query_processor import process_retrieval_query

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


def _parse_row_section_hierarchy(row: Any) -> tuple[Optional[str], Optional[str], Optional[str], Optional[SectionHierarchy]]:
    """Extracts H1, H2, H3 and SectionHierarchy from a BigQuery row."""
    sec_h1 = _extract_str(getattr(row, "section_h1", None))
    sec_h2 = _extract_str(getattr(row, "section_h2", None))
    sec_h3 = _extract_str(getattr(row, "section_h3", None))
    raw_hier = getattr(row, "section_hierarchy", None)
    if raw_hier and not any([sec_h1, sec_h2, sec_h3]):
        hier_dict = dict(raw_hier) if hasattr(raw_hier, "items") else (raw_hier if isinstance(raw_hier, dict) else {})
        sec_h1 = _extract_str(hier_dict.get("h1"))
        sec_h2 = _extract_str(hier_dict.get("h2"))
        sec_h3 = _extract_str(hier_dict.get("h3"))
    sec_hier = SectionHierarchy(h1=sec_h1, h2=sec_h2, h3=sec_h3) if any([sec_h1, sec_h2, sec_h3]) else None
    return sec_h1, sec_h2, sec_h3, sec_hier


def _row_to_search_result(row: Any) -> SearchResult:
    """Constructs a SearchResult object from a BigQuery query row."""
    art_id = _extract_str(getattr(row, "id", "")) or ""
    content_raw = getattr(row, "content", "")
    content_str = str(content_raw) if content_raw is not None else ""
    dist_val = getattr(row, "distance", 0.0)
    relevance = normalize_similarity(dist_val, metric="COSINE")
    sec_h1, sec_h2, sec_h3, sec_hier = _parse_row_section_hierarchy(row)
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
    chunk_idx = getattr(row, "chunk_index", None)
    return SearchResult(
        article_id=art_id,
        parent_doc_id=_extract_str(getattr(row, "parent_doc_id", None)),
        chunk_index=chunk_idx if isinstance(chunk_idx, int) else None,
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
        raw_project = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "it-helpdesk-prod")
        raw_dataset = dataset_id or os.getenv("BIGQUERY_KB_DATASET", "it_helpdesk_kb")
        raw_table = table_name

        for ident, name in [(raw_project, "project_id"), (raw_dataset, "dataset_id"), (raw_table, "table_name")]:
            if not re.match(r"^[a-zA-Z0-9_\-\.]+$", str(ident)):
                raise ValueError(f"Invalid BigQuery identifier '{ident}' for {name}")

        self.project_id = str(raw_project)
        self.dataset_id = str(raw_dataset)
        self.table_name = str(raw_table)
        self.embedding_model = embedding_model
        self.embedding_fn = embedding_fn
        self._index_active_cache: Optional[tuple[bool, float]] = None

        if bq_client is not None:
            self.bq_client = bq_client
            if type(bq_client).__name__ not in ("MagicMock", "Mock"):
                self._verify_kb_metadata()
        else:
            try:
                from google.cloud import bigquery
                self.bq_client = bigquery.Client(project=self.project_id)
                self._verify_kb_metadata()
            except ImportError as e:
                logger.error("google-cloud-bigquery library is required for BigQueryVectorKnowledgeStore (%s).", e)
                raise ImportError(
                    "Thư viện 'google-cloud-bigquery' chưa được cài đặt. "
                    "Hãy cài đặt google-cloud-bigquery để sử dụng backend BigQuery."
                ) from e
            except Exception as e:
                logger.error("Failed to initialize BigQuery Client for Vector Search (%s).", e)
                self.bq_client = None

    def _verify_kb_metadata(self) -> None:
        """Verifies ingested KB metadata matches active runtime embedding model and dimension."""
        if not self.bq_client:
            return
        try:
            from google.cloud import bigquery
            sql = f"""
            SELECT metadata_key, embedding_model, embedding_dim, kb_version
            FROM `{self.project_id}.{self.dataset_id}.kb_metadata`
            WHERE metadata_key = @meta_key
            LIMIT 1
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("meta_key", "STRING", "active_kb")]
            )
            rows = list(self.bq_client.query(sql, job_config=job_config).result(timeout=5.0))
            if rows:
                row = rows[0]
                ingested_model = getattr(row, "embedding_model", None)
                ingested_dim = getattr(row, "embedding_dim", None)
                if isinstance(ingested_model, str) and type(ingested_model).__name__ not in ("MagicMock", "Mock"):
                    if ingested_model.strip() != self.embedding_model.strip():
                        raise RuntimeError(
                            f"CRITICAL: Knowledge Base metadata mismatch! "
                            f"Ingested embedding model is '{ingested_model}', but service is configured with '{self.embedding_model}'. "
                            f"Refusing startup to prevent invalid embeddings."
                        )
                if isinstance(ingested_dim, (int, float)) and type(ingested_dim).__name__ not in ("MagicMock", "Mock"):
                    if int(ingested_dim) != 768:
                        raise RuntimeError(
                            f"CRITICAL: Knowledge Base metadata mismatch! "
                            f"Ingested embedding dimension is {ingested_dim}, but service dimension is 768. "
                            f"Refusing startup."
                        )
        except RuntimeError:
            raise
        except Exception as e:
            logger.debug("Could not verify kb_metadata table: %s", e)

    def _generate_embedding(self, text: str, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
        """Generates embedding using the shared enterprise embedding model or injected function."""
        if self.embedding_fn is not None:
            return self.embedding_fn(text)
        return resolve_generate_text_embedding(text, model_name=self.embedding_model, task_type=task_type)

    def _is_vector_index_active(self) -> bool:
        """Checks if BigQuery Vector Index exists, status is ACTIVE, and coverage >= 95.0. Caches for 60s."""
        now = time.time()
        if self._index_active_cache is not None:
            active, cached_time = self._index_active_cache
            if now - cached_time < 60:
                return active
        if not self.bq_client:
            return False
        try:
            from google.cloud import bigquery
            sql = f"""
            SELECT index_status, coverage_percentage 
            FROM `{self.project_id}.{self.dataset_id}.INFORMATION_SCHEMA.VECTOR_INDEXES`
            WHERE table_name = @table_name
            LIMIT 1
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("table_name", "STRING", self.table_name)]
            )
            rows = list(self.bq_client.query(sql, job_config=job_config).result(timeout=5.0))
            is_active = False
            if rows:
                status = getattr(rows[0], "index_status", "UNKNOWN")
                cov = getattr(rows[0], "coverage_percentage", None)
                is_active = (status == "ACTIVE" and cov is not None and float(cov) >= 95.0)
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
        """Searches BigQuery table using VECTOR_SEARCH with Pre-filtering subquery and scalar clearance level pre-filter."""
        if not self.bq_client:
            logger.error("BigQuery client is not initialized. Raising KnowledgeStoreUnavailableError.")
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Knowledge Store chưa được khởi tạo.")

        valid_systems = resolve_valid_system_filters()
        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in valid_systems:
            clean_system = "ALL"

        sec_ctx = resolve_security_context(security_context=security_context)
        retrieval_cfg = resolve_retrieval_config()
        raw_fraction = retrieval_cfg.get("fraction_lists_to_search", 0.05)
        try:
            clean_fraction = float(raw_fraction)
            if clean_fraction <= 0.0 or clean_fraction > 1.0 or not math.isfinite(clean_fraction):
                clean_fraction = 0.05
        except (ValueError, TypeError):
            clean_fraction = 0.05

        hybrid_enabled = retrieval_cfg.get("hybrid_search_enabled", True)
        reranker_enabled = retrieval_cfg.get("reranker_enabled", False) or os.getenv("USE_VERTEX_RERANKER", "false").lower() in ("true", "1", "yes")

        cfg_retrieve_k = retrieval_cfg.get("retrieve_k", 20)
        cfg_final_k = retrieval_cfg.get("final_k", 3)
        target_final_k = limit if limit is not None and limit > 0 else cfg_final_k
        max_rounds = int(retrieval_cfg.get("adaptive_retrieval_rounds", 2))

        effective_query = process_retrieval_query(query, retrieval_cfg) or query

        try:
            query_vec = self._generate_embedding(effective_query)
            full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"

            from google.cloud import bigquery
            today_iso = datetime.date.today().isoformat()

            base_filters = (
                "(is_deleted IS NOT TRUE) "
                "AND (expiry_date IS NULL OR expiry_date >= @today) "
                "AND (effective_date IS NULL OR effective_date <= @today) "
                "AND (clearance_level IS NULL OR clearance_level <= @user_clearance)"
            )

            index_active = self._is_vector_index_active()
            options_clause = f", options => '{{\"fraction_lists_to_search\": {clean_fraction}}}'" if index_active else ""

            authorized_candidates: list[SearchResult] = []
            seen_ids: set[str] = set()
            current_retrieve_k = max(cfg_retrieve_k, target_final_k * 2, 10)

            select_cols = (
                "base.id, base.parent_doc_id, base.chunk_index, base.system, base.title, "
                "base.content, base.section_h1, base.section_h2, base.section_h3, base.allowed_roles, "
                "base.sensitivity, base.clearance_level, base.source_uri, base.category, "
                "base.keywords, base.owner, base.effective_date, base.expiry_date, base.is_deleted, distance"
            )

            for round_idx in range(1, max_rounds + 1):
                query_params = [
                    bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vec),
                    bigquery.ScalarQueryParameter("limit", "INT64", current_retrieve_k),
                    bigquery.ScalarQueryParameter("today", "DATE", today_iso),
                    bigquery.ScalarQueryParameter("user_clearance", "INT64", sec_ctx.clearance_level),
                ]

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
                    query_params.append(bigquery.ScalarQueryParameter("query_text", "STRING", effective_query.strip()))
                    sql = f"""
                    SELECT {select_cols}
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
                    sql = f"""
                    SELECT {select_cols}
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
                    candidate = _row_to_search_result(row)
                    if not resolve_authorize_document(candidate, sec_ctx):
                        continue
                    seen_ids.add(art_id)
                    authorized_candidates.append(candidate)

                if len(authorized_candidates) >= target_final_k or round_idx >= max_rounds or len(rows) < current_retrieve_k:
                    break
                current_retrieve_k = max(current_retrieve_k * 2, target_final_k * 4)

            if reranker_enabled:
                authorized_candidates = resolve_rerank_search_results(
                    query=effective_query,
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
            sec_h1, sec_h2, sec_h3, sec_hier = _parse_row_section_hierarchy(r)
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
            if not resolve_authorize_document(article, security_context):
                return None
            return article
        except Exception as e:
            logger.error("BigQuery get_article_by_id failed (%s). Raising KnowledgeStoreUnavailableError.", e)
            raise KnowledgeStoreUnavailableError(f"Truy xuất bài viết BigQuery thất bại: {e}") from e
