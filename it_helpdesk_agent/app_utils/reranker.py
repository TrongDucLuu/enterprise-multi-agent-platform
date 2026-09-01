"""
Vertex AI / Discovery Engine Ranking API Reranker for Enterprise RAG.

Provides semantic reranking using Vertex AI's Ranking API (`semantic-ranker-512`).
Supports reranking candidate search results from BigQuery Vector / Hybrid Search,
with fail-safe fallback to candidate order if the ranking service is disabled or unavailable.
"""

import os
import re
import logging
from typing import Optional, Any
from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import SearchResult

logger = logging.getLogger(__name__)

DEFAULT_RANKER_MODEL = "semantic-ranker-512@latest"


def rerank_search_results(
    query: str,
    candidates: list[SearchResult],
    top_n: Optional[int] = None,
    project_id: Optional[str] = None,
    ranking_model: str = DEFAULT_RANKER_MODEL,
    use_reranker: Optional[bool] = None,
) -> list[SearchResult]:
    """
    Reranks a list of candidate SearchResult objects using Vertex AI Ranking API.

    Args:
        query: The user query.
        candidates: List of SearchResult objects from initial vector/hybrid search.
        top_n: Number of top results to return (defaults to len(candidates)).
        project_id: GCP project ID for RankingConfig path.
        ranking_model: Ranking model name (e.g. 'semantic-ranker-512@latest').
        use_reranker: Explicit toggle. If None, checks USE_VERTEX_RERANKER env var.

    Returns:
        Reranked list of SearchResult objects (up to top_n items).
    """
    if not candidates:
        return []

    target_top_n = top_n if top_n is not None and top_n > 0 else len(candidates)
    target_top_n = min(target_top_n, len(candidates))

    if use_reranker is None:
        use_reranker = os.getenv("USE_VERTEX_RERANKER", "false").lower() in ("true", "1", "yes")

    if not use_reranker:
        return candidates[:target_top_n]

    proj = project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID", "default")

    try:
        from google.cloud import discoveryengine_v1 as discoveryengine

        client = discoveryengine.RankServiceClient()
        ranking_config = f"projects/{proj}/locations/global/rankingConfigs/default_ranking_config"

        # Pass clean text to cross-encoder (strip XML wrapping tags if present)
        records = [
            discoveryengine.RankingRecord(
                id=c.article_id,
                title=c.title,
                content=re.sub(r"<[^>]+>", "", c.snippet).strip()[:1000],
            )
            for c in candidates
        ]

        request = discoveryengine.RankRequest(
            ranking_config=ranking_config,
            model=ranking_model,
            query=query,
            records=records,
            top_n=target_top_n,
        )

        response = client.rank(request=request, timeout=1.5)

        # Map reranked records back to SearchResult candidates
        id_to_candidate = {c.article_id: c for c in candidates}
        reranked_results: list[SearchResult] = []

        for record in response.records:
            if record.id in id_to_candidate:
                cand = id_to_candidate[record.id]
                # Normalize ranking score to [0.0, 1.0] if provided
                raw_score = float(record.score) if hasattr(record, "score") and record.score is not None else cand.relevance_score
                # Bound score to [0.0, 1.0]
                norm_score = max(0.0, min(1.0, raw_score))
                reranked_cand = cand.model_copy(update={"relevance_score": norm_score})
                reranked_results.append(reranked_cand)

        # Append any missing candidates in original relative order up to target_top_n
        if len(reranked_results) < target_top_n:
            seen_ids = {r.article_id for r in reranked_results}
            for c in candidates:
                if c.article_id not in seen_ids:
                    reranked_results.append(c)
                    seen_ids.add(c.article_id)
                if len(reranked_results) >= target_top_n:
                    break

        return reranked_results[:target_top_n]

    except Exception as e:
        logger.warning(
            "Vertex AI Ranking API call failed (%s). Falling back to original search ordering.", e
        )
        return candidates[:target_top_n]
