"""
Vertex AI / Discovery Engine Ranking API Reranker for Enterprise RAG.

Provides semantic reranking using Vertex AI's Ranking API (`semantic-ranker-512`).
Supports reranking candidate search results from BigQuery Vector / Hybrid Search,
with fail-safe fallback to candidate order if the ranking service is disabled or unavailable.
"""

import os
import re
import unicodedata
import logging
from typing import Optional, Any
from agent_core.tools.enterprise_rag_mcp.rag_models import SearchResult

logger = logging.getLogger(__name__)

DEFAULT_RANKER_MODEL = "semantic-ranker-512@latest"


def _normalize_text(text: str) -> str:
    """Strip diacritics and lowercase text for robust cross-lingual / Vietnamese matching."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c)).replace("đ", "d").replace("Đ", "d")


def _fallback_cross_rerank(query: str, candidates: list[SearchResult], top_n: int) -> list[SearchResult]:
    """
    Deterministic cross-field ranking fallback when Vertex AI Ranking API is unavailable or offline.
    Scores candidates on query token coverage, exact title/keyword matching, and base retriever relevance.
    """
    if not candidates:
        return []

    norm_q = _normalize_text(query).strip()
    q_words = [w for w in re.findall(r"[\w\-]+", norm_q) if len(w) > 1]
    q_tokens = set(q_words)
    q_bigrams = {" ".join(q_words[i:i+2]) for i in range(len(q_words)-1)} if len(q_words) > 1 else set()

    scored: list[tuple[float, int, SearchResult]] = []
    for idx, cand in enumerate(candidates):
        norm_title = _normalize_text(cand.title or "")
        norm_snippet = _normalize_text(re.sub(r"<[^>]+>", "", cand.snippet or ""))
        norm_id = _normalize_text(cand.article_id or "")
        cand_keywords = [_normalize_text(k) for k in (cand.keywords or [])]

        title_tokens = set(re.findall(r"[\w\-]+", norm_title))
        snippet_tokens = set(re.findall(r"[\w\-]+", norm_snippet))

        # Base retriever score preservation (strong base weight)
        score = float(cand.relevance_score or 0.0) * 8.0

        # Exact Article ID match in query
        if norm_id and (norm_id in norm_q or norm_id in q_tokens):
            score += 10.0

        # Bigram phrase matching in title
        matching_bigrams = sum(1 for bg in q_bigrams if bg in norm_title)
        if matching_bigrams > 0:
            score += min(matching_bigrams * 3.0, 9.0)

        # Exact phrase in title or title in query
        if len(norm_title) > 5 and norm_title in norm_q:
            score += 6.0
        elif len(norm_q) > 5 and norm_q in norm_title:
            score += 6.0

        # Keywords in query
        for kw in cand_keywords:
            if kw and len(kw) >= 2 and (kw in norm_q or kw in q_tokens):
                score += 4.0

        if q_tokens:
            # Title token coverage
            title_overlap = len(q_tokens.intersection(title_tokens))
            score += (title_overlap / len(q_tokens)) * 5.0

            # Content token coverage
            content_overlap = len(q_tokens.intersection(snippet_tokens))
            score += (content_overlap / len(q_tokens)) * 1.5

        scored.append((score, idx, cand))

    # Sort descending by cross-score with stable tie-breaking
    scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)

    reranked = []
    for score, _, cand in scored[:top_n]:
        # Normalize score into [0.5, 1.0] for reranked results
        norm_score = max(0.0, min(1.0, 0.5 + (min(score, 30.0) / 60.0)))
        reranked.append(cand.model_copy(update={"relevance_score": norm_score}))
    return reranked


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
    if use_reranker is None:
        use_reranker = os.getenv("USE_VERTEX_RERANKER", "false").lower() in ("true", "1", "yes")

    if not use_reranker:
        return candidates[:target_top_n]

    use_vertex_live = os.getenv("USE_VERTEX_RERANKER", "false").lower() in ("true", "1", "yes")
    if not use_vertex_live:
        return _fallback_cross_rerank(query, candidates, target_top_n)

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
                raw_score = float(record.score) if hasattr(record, "score") and record.score is not None else cand.relevance_score
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
            "Vertex AI Ranking API call unavailable or failed (%s). Falling back to cross-field semantic ranker.", e
        )
        return _fallback_cross_rerank(query, candidates, target_top_n)
