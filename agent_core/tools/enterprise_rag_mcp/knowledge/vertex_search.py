"""
Google Cloud Vertex AI Search / Discovery Engine Knowledge Store Adapter.
"""
import os
import json
import logging
from typing import Optional, Any

from .base import (
    BaseKnowledgeStore,
    KnowledgeStoreUnavailableError,
    resolve_security_context,
    resolve_retrieval_config,
    resolve_rerank_search_results,
    resolve_authorize_document,
    _extract_str,
    _extract_bool,
    _extract_list,
)
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
        retrieval_cfg = resolve_retrieval_config()
        effective_query = process_retrieval_query(query, retrieval_cfg) or query

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
                query=effective_query.strip(),
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

            if not resolve_authorize_document(res, sec_ctx):
                continue

            search_results.append(res)

        # Apply reranker if enabled
        retrieval_cfg = resolve_retrieval_config()
        reranker_enabled = retrieval_cfg.get("reranker_enabled", False) or os.getenv("USE_VERTEX_RERANKER", "false").lower() in ("true", "1", "yes")
        if reranker_enabled and search_results:
            search_results = resolve_rerank_search_results(
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
                    if not resolve_authorize_document(article, security_context):
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
