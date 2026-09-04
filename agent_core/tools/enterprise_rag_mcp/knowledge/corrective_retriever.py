"""
Enterprise Corrective Retrieval Loop (Phase 1 Item C [R3]).

Provides:
- Retrieval confidence assessment based on relevance score thresholding and result sufficiency.
- Heuristic and synonym-expanded query refinement for corrective retrieval rounds.
- Corrective retrieval loop orchestrator: Query -> Retrieve -> Check Confidence -> If Low: Refine & Re-retrieve (bounded by adaptive_retrieval_rounds).
"""

import re
import unicodedata
import logging
from typing import Callable, Optional, Any
from agent_core.tools.enterprise_rag_mcp.rag_models import SearchResult

logger = logging.getLogger(__name__)

DEFAULT_CONFIDENCE_THRESHOLD = 0.65
DEFAULT_MAX_ROUNDS = 2

# Common IT keyword synonym and query broadening mappings (Vietnamese and English)
_IT_SYNONYMS: dict[str, list[str]] = {
    "lỗi": ["sự cố", "error", "fail"],
    "sự cố": ["lỗi", "troubleshoot", "incident"],
    "hủy": ["cancel", "delete", "hủy bỏ"],
    "kết nối": ["connection", "connect", "logon"],
    "mật khẩu": ["password", "reset", "auth"],
    "tài khoản": ["account", "user", "access"],
    "cấu hình": ["config", "setup", "logon"],
    "đồng bộ": ["sync", "integration", "webhook"],
    "phê duyệt": ["approval", "approve", "workflow"],
    "tồn kho": ["inventory", "stock", "storage"],
    "hóa đơn": ["billing", "invoice", "document"],
}


def _normalize_text(text: str) -> str:
    """Strips diacritics and lowercases text for matching."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c)).replace("đ", "d").replace("Đ", "d")


def evaluate_retrieval_confidence(
    results: list[SearchResult],
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    min_results: int = 1,
) -> bool:
    """
    Evaluates whether the retrieved results meet the enterprise confidence threshold.
    Returns True if:
    1. At least `min_results` candidates are returned.
    2. The top candidate's relevance_score >= `threshold`.
    """
    if not results or len(results) < min_results:
        return False

    top_score = max(float(getattr(r, "relevance_score", 0.0) or 0.0) for r in results)
    return top_score >= threshold


def refine_corrective_query(
    original_query: str,
    round_index: int,
    base_keywords: Optional[list[str]] = None,
) -> str:
    """
    Generates a refined, broadened query when previous retrieval round had low confidence.
    
    Strategies:
    - Round 2: Extract technical codes, remove restrictive filler phrases, and add domain synonyms.
    - Round 3+: Fall back to core entity keywords and technical tokens.
    """
    if not original_query or not str(original_query).strip():
        return ""

    raw_query = str(original_query).strip()

    # Extract technical code tokens (e.g. ME21N, OB52, ERP-KB-001, 504, 401)
    tech_codes = re.findall(
        r"\b([A-Z0-9]+(?:[-_][A-Z0-9]+)+|[A-Z]{1,4}[0-9]{2,5}[A-Z0-9]*|[0-9]{3,4})\b",
        raw_query,
        re.IGNORECASE,
    )

    # Word tokens
    words = re.findall(r"[\wÀ-ỹ\-]+", raw_query)
    
    # Filter out common stop / noise words in Vietnamese & English
    noise_words = {
        "là", "gì", "như", "thế", "nào", "ở", "đâu", "bao", "nhiêu", "cho", "tôi",
        "hỏi", "xin", "vui", "lòng", "cần", "muốn", "biết", "cách", "hướng", "dẫn",
        "làm", "sao", "được", "không", "nhỉ", "và", "hoặc", "về", "có", "phải",
        "the", "a", "an", "is", "are", "how", "what", "where", "can", "you", "please"
    }
    salient_words = [w for w in words if w.lower() not in noise_words and len(w) > 1]

    if round_index == 2:
        # Expand salient words with IT domain synonyms
        expanded_terms = list(salient_words)
        for w in salient_words:
            w_norm = _normalize_text(w)
            for key, syns in _IT_SYNONYMS.items():
                if _normalize_text(key) == w_norm:
                    for s in syns[:2]:
                        if s not in expanded_terms:
                            expanded_terms.append(s)
        
        # Ensure technical codes are placed first for high retrieval weight
        unique_tokens = []
        for tc in tech_codes:
            if tc not in unique_tokens:
                unique_tokens.append(tc)
        for t in expanded_terms:
            if t not in unique_tokens:
                unique_tokens.append(t)
        
        refined = " ".join(unique_tokens).strip()
        return refined if refined else raw_query

    # Round 3+: High-precision core tokens only
    core_tokens = list(tech_codes)
    for w in salient_words:
        if len(w) >= 3 and w not in core_tokens:
            core_tokens.append(w)

    refined = " ".join(core_tokens).strip()
    return refined if refined else raw_query


def merge_candidate_results(
    existing: list[SearchResult],
    new_candidates: list[SearchResult],
    limit: int = 3,
) -> list[SearchResult]:
    """
    Merges search results across iterative rounds, deduplicating by article_id and chunk_index,
    retaining the highest relevance_score for duplicates, and returning candidates sorted descending.
    """
    by_key: dict[tuple[str, Optional[int]], SearchResult] = {}

    for cand in existing + new_candidates:
        key = (cand.article_id, cand.chunk_index)
        if key not in by_key:
            by_key[key] = cand
        else:
            prev_score = float(by_key[key].relevance_score or 0.0)
            curr_score = float(cand.relevance_score or 0.0)
            if curr_score > prev_score:
                by_key[key] = cand

    merged = list(by_key.values())
    merged.sort(key=lambda x: float(x.relevance_score or 0.0), reverse=True)
    return merged[:limit]
