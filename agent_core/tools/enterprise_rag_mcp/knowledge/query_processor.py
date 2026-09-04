"""
Enterprise Retrieval Query Processor (Non-LLM Preprocessing & LLM Query Rewrite).

Provides:
- Non-LLM Preprocessing (A1): Unicode normalization, conversational stop-phrase stripping,
  punctuation cleanup while preserving technical codes (ME21N, OB52, HDW-KB-*, error codes).
- Fast LLM Query Rewrite (A2): High-signal search keyword expansion via fast Gemini model
  with strict timeout (1.5s) and fail-safe fallback to preprocessed query.
- Orchestrator (process_retrieval_query): Flag-driven query optimization pipeline.
"""

import os
import re
import unicodedata
import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Optional, Any

from .base import resolve_retrieval_config

logger = logging.getLogger(__name__)

# Compile regex patterns for non-LLM preprocessing
# Conversational filler phrases in Vietnamese and English to strip from query start/end
_VIETNAMESE_STOP_PHRASES = [
    r"^(?:vui\s+lòng\s+)?(?:cho\s+tôi\s+hỏi|xin\s+hỏi|hãy\s+cho\s+biết|cho\s+biết|hỏi\s+về|tìm\s+kiếm)\b",
    r"^(?:làm\s+sao\s+để|làm\s+thế\s+nào\s+để|hướng\s+dẫn\s+tôi|hướng\s+dẫn\s+cách|hướng\s+dẫn|cách\s+để|cách\s+xử\s+lý|cách\s+khắc\s+phục|cách\s+giải\s+quyết|cách\s+sửa)\b",
    r"^(?:tôi\s+muốn\s+hỏi|tôi\s+cần\s+hỗ\s+trợ|tôi\s+cần\s+biết|giúp\s+tôi|làm\s+ơn)\b",
    r"\b(?:là\s+gì|như\s+thế\s+nào|ở\s+đâu|bao\s+nhiêu|ra\s+sao|thế\s+nào|được\s+không|nhỉ|với|ạ)\??$",
]

_ENGLISH_STOP_PHRASES = [
    r"^(?:can\s+you\s+(?:please\s+)?(?:tell\s+me|show\s+me|help\s+me|explain)|please\s+tell\s+me|tell\s+me\s+about|could\s+you\s+(?:please\s+)?explain|explain\s+to\s+me|explain)\b",
    r"^(?:how\s+to|how\s+do\s+i|how\s+can\s+i|instructions\s+for|guide\s+on|guide\s+for|show\s+me\s+how\s+to)\b",
    r"^(?:what\s+is|what\s+are|where\s+can\s+i\s+find|where\s+to\s+find|where\s+is|who\s+is)\b",
    r"^(?:please\s+help\s+me\s+with|please\s+help\s+me|i\s+need\s+help\s+with|help\s+with|please\s+provide|please\s+find)\b",
    r"\b(?:please|thank\s+you|thanks)\??$",
]

_COMPILED_STOP_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _VIETNAMESE_STOP_PHRASES + _ENGLISH_STOP_PHRASES]

# Technical patterns to preserve (IDs, error codes, transaction codes)
_TECH_CODE_PATTERN = re.compile(
    r"\b([A-Z0-9]+(?:[-_][A-Z0-9]+)+|[A-Z]{1,4}[0-9]{2,5}[A-Z0-9]*|[0-9]{3,4}|[A-Z]{2,6})\b"
)


def preprocess_query(query: str) -> str:
    """
    Non-LLM deterministic query preprocessing (A1).
    - Normalizes Unicode (NFC).
    - Strips leading/trailing whitespace.
    - Strips conversational prefix/suffix questions and filler words.
    - Cleans punctuation while preserving technical tokens, codes, and hyphens.
    - Preserves case for technical codes (ME21N, OB52, KB-001) while normalizing text.
    """
    if not query or not str(query).strip():
        return ""

    raw = str(query).strip()
    # Normalize Unicode (NFC)
    normalized = unicodedata.normalize("NFC", raw)

    # Iteratively strip conversational stop-phrases
    cleaned = normalized
    prev = ""
    while cleaned != prev:
        prev = cleaned
        for pat in _COMPILED_STOP_PATTERNS:
            cleaned = pat.sub("", cleaned).strip()

    # If all tokens were stripped (e.g. user just entered "là gì"), revert to normalized original
    if not cleaned:
        cleaned = normalized

    # Clean extraneous punctuation (?, !, quotes, parens) but keep technical symbols (- _)
    # Replace non-alphanumeric chars (except Vietnamese diacritics, hyphens, and underscores) with space
    cleaned = re.sub(r"[^\w\s\-_À-ỹ]", " ", cleaned)
    # Collapse multiple whitespaces
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    return cleaned if cleaned else raw


def _invoke_llm_rewrite_sync(
    query: str,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    model_name: Optional[str] = None,
) -> str:
    """Synchronously calls Vertex AI / Gemini to rewrite query into keyword-dense retrieval query."""
    target_project = project_id or os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID")
    target_location = location or os.getenv("MODEL_LOCATION", "us-central1")
    target_model = model_name or os.getenv("FAST_LLM_MODEL", "gemini-2.5-flash")

    system_instruction = (
        "You are an enterprise search query optimizer. "
        "Given a user support query, output a concise, keyword-rich search query optimized for "
        "enterprise documentation retrieval (Vector and BM25). "
        "Strictly preserve technical error codes, transaction codes (e.g. ME21N, OB52), system names (ERP, HRM, CRM), "
        "and article IDs (e.g. HDW-KB-001). "
        "Remove conversational filler words. "
        "Output ONLY the optimized search keywords on a single line with no markdown, punctuation, or commentary."
    )

    try:
        from google import genai
        client = genai.Client(vertexai=True, project=target_project, location=target_location)
        response = client.models.generate_content(
            model=target_model,
            contents=query,
            config=genai.types.GenerateContentConfig(
                system_instruction=system_instruction,
                temperature=0.0,
                max_output_tokens=64,
            ),
        )
        if response and response.text:
            rewritten = response.text.strip().replace("\n", " ")
            return rewritten if rewritten else query
    except Exception as exc:
        logger.debug("Vertex AI / google.genai query rewrite failed: %s", exc)

    # Fallback to vertexai generative_models if genai client is not available
    try:
        import vertexai
        from vertexai.generative_models import GenerativeModel, GenerationConfig
        if target_project:
            vertexai.init(project=target_project, location=target_location)
        model = GenerativeModel(
            model_name=target_model,
            system_instruction=[system_instruction],
        )
        resp = model.generate_content(
            query,
            generation_config=GenerationConfig(
                temperature=0.0,
                max_output_tokens=64,
            ),
        )
        if resp and resp.text:
            rewritten = resp.text.strip().replace("\n", " ")
            return rewritten if rewritten else query
    except Exception as exc:
        logger.debug("vertexai.generative_models query rewrite failed: %s", exc)

    return preprocess_query(query)


def rewrite_query_with_llm(
    query: str,
    project_id: Optional[str] = None,
    location: Optional[str] = None,
    model_name: Optional[str] = None,
    timeout: float = 1.5,
) -> str:
    """
    LLM Query Rewrite (A2) with hard timeout and graceful fallback.
    - Executes LLM rewrite with a strict timeout (default 1.5s).
    - If LLM fails, times out, or offline, falls back gracefully to preprocess_query(query).
    - Never throws an exception to caller.
    """
    if not query or not str(query).strip():
        return ""

    preprocessed = preprocess_query(query)

    # Fast path: in offline mode or when mock embeddings are forced, skip LLM network call
    use_vertex = os.getenv("USE_VERTEX_EMBEDDING", "false").lower() in ("true", "1", "yes")
    has_gcp_project = bool(os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("PROJECT_ID"))
    if not use_vertex and not has_gcp_project:
        return preprocessed

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _invoke_llm_rewrite_sync,
                query=query,
                project_id=project_id,
                location=location,
                model_name=model_name,
            )
            return future.result(timeout=timeout)
    except FuturesTimeoutError:
        logger.warning("LLM Query Rewrite timed out after %.1fs. Falling back to preprocessed query.", timeout)
        return preprocessed
    except Exception as e:
        logger.warning("LLM Query Rewrite encountered error (%s). Falling back to preprocessed query.", e)
        return preprocessed


def process_retrieval_query(
    query: str,
    retrieval_cfg: Optional[dict[str, Any]] = None,
) -> str:
    """
    Main entry point for Retrieval Query Optimization.
    Inspects retrieval configuration flags:
    - query_preprocessing_enabled (bool, default False)
    - query_rewrite_enabled (bool, default False)

    Order of execution:
    1. If query_rewrite_enabled: runs rewrite_query_with_llm (which includes fallback to preprocessing).
    2. Else if query_preprocessing_enabled: runs preprocess_query.
    3. Else: returns stripped original query.
    """
    if not query or not str(query).strip():
        return ""

    cfg = retrieval_cfg if retrieval_cfg is not None else resolve_retrieval_config()
    preprocessing_enabled = bool(cfg.get("query_preprocessing_enabled", False))
    rewrite_enabled = bool(cfg.get("query_rewrite_enabled", False))

    if rewrite_enabled:
        return rewrite_query_with_llm(query)
    elif preprocessing_enabled:
        return preprocess_query(query)
    else:
        return str(query).strip()
