"""
Chunking Strategies and Document Processor for Enterprise Knowledge Base.
Supports section-aware hierarchical chunking, recursive text splitting, and deterministic ID generation.
"""

import hashlib
import logging
from datetime import datetime, timezone
from typing import Optional, Any

from it_helpdesk_agent.app_utils.system_config import (
    get_configured_systems,
    get_valid_system_filters,
    get_chunking_config,
)
from scripts.ingest.parsers import PARSER_VERSION
from scripts.ingest.embedders import EMBEDDING_MODEL, EMBEDDING_DIM

CHUNKER_VERSION = "1.0.0"

logger = logging.getLogger("ingest.chunkers")


def is_well_structured(
    sections: Optional[list[dict[str, Any]]],
    max_chunk_size: int = 1200,
    max_section_ratio: float = 0.65,
    min_avg_length: int = 100,
) -> bool:
    """
    Evaluates whether a document's extracted sections provide clean structural boundaries:
    - At least 2 sections.
    - No single section occupies more than max_section_ratio (65%) of total document text.
    - Average length per section is at least min_avg_length (100 chars).
    """
    if not sections or len(sections) < 2:
        return False

    total_len = sum(len(s.get("content", "")) for s in sections)
    if total_len == 0:
        return False

    avg_len = total_len / len(sections)
    if avg_len < min_avg_length:
        return False

    for s in sections:
        sec_len = len(s.get("content", ""))
        if (sec_len / total_len) > max_section_ratio:
            return False

    return True


def chunk_by_sections(
    sections: list[dict[str, Any]],
    max_chunk_size: int = 1200,
    overlap: int = 150,
    return_metadata: bool = False,
) -> Any:
    """
    Chunks document section-by-section, keeping headings attached to content.
    If a section exceeds max_chunk_size, it is recursively split within that section's scope.
    """
    chunks = []
    chunk_meta = []
    for sec in sections:
        heading = sec.get("heading", "").strip()
        content = sec.get("content", "").strip()
        hierarchy = sec.get("hierarchy") or {
            "h1": heading or None,
            "h2": None,
            "h3": None,
        }
        if not content:
            continue

        header_prefix = f"## {heading}\n\n" if heading else ""
        full_sec_text = f"{header_prefix}{content}".strip()

        if len(full_sec_text) <= max_chunk_size:
            chunks.append(full_sec_text)
            chunk_meta.append(hierarchy)
        else:
            # Section exceeds max_chunk_size -> recursive split content within section scope
            sub_max_size = max(200, max_chunk_size - len(header_prefix))
            sub_chunks = chunk_text(content, max_chunk_size=sub_max_size, overlap=overlap)
            for sub in sub_chunks:
                chunks.append(f"{header_prefix}{sub}".strip())
                chunk_meta.append(hierarchy)

    if return_metadata:
        return [{"text": c, "hierarchy": m} for c, m in zip(chunks, chunk_meta) if c]
    return [c for c in chunks if c]


def chunk_text(
    text: str,
    max_chunk_size: int = 1200,
    overlap: int = 150,
    separators: Optional[list[str]] = None,
) -> list[str]:
    """
    Splits text recursively using prioritized separators:
    \n\n\n -> \n\n -> \n -> .  -> hard character split.
    """
    if not text or not text.strip():
        return []

    text = text.strip()
    if len(text) <= max_chunk_size:
        return [text]

    if separators is None:
        separators = ["\n\n\n", "\n\n", "\n", ". "]

    chosen_sep = None
    for sep in separators:
        if sep in text:
            chosen_sep = sep
            break

    if chosen_sep is None:
        # Fallback to hard character split
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + max_chunk_size, len(text))
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(start + 1, end - overlap)
        return chunks

    splits = text.split(chosen_sep)
    chunks = []
    current_parts: list[str] = []
    current_len = 0
    remaining_seps = separators[separators.index(chosen_sep) + 1:]

    for part in splits:
        if not part.strip():
            continue
        part_len = len(part)

        if part_len > max_chunk_size:
            if current_parts:
                merged = chosen_sep.join(current_parts).strip()
                if merged:
                    chunks.append(merged)
                current_parts = []
                current_len = 0
            sub_chunks = chunk_text(part, max_chunk_size=max_chunk_size, overlap=overlap, separators=remaining_seps)
            chunks.extend(sub_chunks)
        elif current_len + (len(chosen_sep) if current_parts else 0) + part_len <= max_chunk_size:
            current_parts.append(part)
            current_len += (len(chosen_sep) if len(current_parts) > 1 else 0) + part_len
        else:
            merged = chosen_sep.join(current_parts).strip()
            if merged:
                chunks.append(merged)

            overlap_parts: list[str] = []
            overlap_len = 0
            for p in reversed(current_parts):
                if overlap_len + len(p) <= overlap:
                    overlap_parts.insert(0, p)
                    overlap_len += len(p) + len(chosen_sep)
                else:
                    break
            current_parts = overlap_parts + [part]
            current_len = sum(len(p) for p in current_parts) + (len(chosen_sep) * max(0, len(current_parts) - 1))

    if current_parts:
        merged = chosen_sep.join(current_parts).strip()
        if merged:
            chunks.append(merged)

    return chunks


def process_document(
    doc_info: dict[str, Any],
    default_system: Optional[str] = None
) -> list[dict[str, Any]]:
    """
    Validates, chunks, and prepares a document for embedding and ingestion.
    Applies customer-configurable tiered chunking strategy (auto, fixed, semantic),
    generates deterministic article IDs based on SHA-256(system:source_uri:title:idx)
    and computes content_hash for CDC change tracking.
    """
    valid_systems = get_valid_system_filters()
    configured_systems = get_configured_systems()

    raw_system = doc_info.get("system") or default_system
    if not raw_system:
        # Try inferring system from title/filename
        text_for_infer = (doc_info.get("title", "") + " " + doc_info.get("source_uri", "")).upper()
        for s in configured_systems:
            if s in text_for_infer:
                raw_system = s
                break

    if not raw_system:
        raw_system = configured_systems[0] if configured_systems else "ERP"
        logger.info("Defaulting system to '%s' for '%s'", raw_system, doc_info.get("title"))

    system_clean = raw_system.strip().upper()
    if system_clean not in valid_systems or system_clean == "ALL":
        raise ValueError(
            f"Hệ thống '{raw_system}' không hợp lệ hoặc là từ khóa dành riêng. "
            f"Các hệ thống được hỗ trợ: {configured_systems}"
        )

    title = doc_info.get("title", "Untitled Document")
    category = doc_info.get("category", "Operations")
    content = doc_info.get("content", "")
    source_uri = doc_info.get("source_uri", "")
    owner = doc_info.get("owner")
    effective_date = doc_info.get("effective_date")
    expiry_date = doc_info.get("expiry_date")
    raw_keywords = doc_info.get("keywords", [])
    sections = doc_info.get("sections", [])

    # Load system-specific or global chunking configuration
    chunking_cfg = get_chunking_config(system_clean)
    strategy = chunking_cfg.get("strategy", "auto")
    max_chunk_size = chunking_cfg.get("max_chunk_size", 1200)
    overlap = chunking_cfg.get("overlap", 150)
    max_sec_ratio = chunking_cfg.get("well_structured_max_section_ratio", 0.65)
    min_avg_len = chunking_cfg.get("well_structured_min_avg_section_length", 100)

    chunk_items: list[dict[str, Any]] = []

    if strategy == "semantic":
        logger.info("Semantic chunking strategy flagged for system '%s' (fallback to structured/recursive)", system_clean)
        if sections and is_well_structured(sections, max_chunk_size=max_chunk_size, max_section_ratio=max_sec_ratio, min_avg_length=min_avg_len):
            chunk_items = chunk_by_sections(sections, max_chunk_size=max_chunk_size, overlap=overlap, return_metadata=True)
        else:
            raw_c = chunk_text(content, max_chunk_size=max_chunk_size, overlap=overlap)
            chunk_items = [{"text": c, "hierarchy": {"h1": title, "h2": None, "h3": None}} for c in raw_c]
    elif strategy == "fixed":
        raw_c = chunk_text(content, max_chunk_size=max_chunk_size, overlap=overlap)
        chunk_items = [{"text": c, "hierarchy": {"h1": title, "h2": None, "h3": None}} for c in raw_c]
    else:  # "auto"
        if sections and is_well_structured(sections, max_chunk_size=max_chunk_size, max_section_ratio=max_sec_ratio, min_avg_length=min_avg_len):
            chunk_items = chunk_by_sections(sections, max_chunk_size=max_chunk_size, overlap=overlap, return_metadata=True)
        else:
            raw_c = chunk_text(content, max_chunk_size=max_chunk_size, overlap=overlap)
            chunk_items = [{"text": c, "hierarchy": {"h1": title, "h2": None, "h3": None}} for c in raw_c]

    processed_articles = []

    for idx, item in enumerate(chunk_items):
        chunk = item["text"]
        section_hierarchy = item.get("hierarchy") or {"h1": title, "h2": None, "h3": None}

        # Generate deterministic ID
        if doc_info.get("id") and len(chunk_items) == 1:
            article_id = doc_info["id"].upper()
        else:
            hasher = hashlib.sha256()
            hasher.update(f"{system_clean}:{source_uri}:{title}:{idx}".encode("utf-8"))
            short_hash = hasher.hexdigest()[:8].upper()
            article_id = f"{system_clean}-KB-{short_hash}"

        chunk_title = title if len(chunk_items) == 1 else f"{title} (Phần {idx + 1}/{len(chunk_items)})"

        # Extract keywords if not provided
        keywords = raw_keywords.copy()
        if not keywords:
            words = [w.strip(".,;:()") for w in chunk_title.lower().split() if len(w) > 2]
            keywords = list(set(words))[:8]

        # Compute content_hash for Change Data Capture (CDC)
        content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()

        processed_articles.append({
            "id": article_id,
            "system": system_clean,
            "title": chunk_title,
            "category": category,
            "content": chunk,
            "keywords": keywords,
            "source_uri": source_uri,
            "owner": owner,
            "effective_date": effective_date,
            "expiry_date": expiry_date,
            "is_deleted": False,
            "deleted_at": None,
            "parser_version": PARSER_VERSION,
            "chunker_version": CHUNKER_VERSION,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dim": EMBEDDING_DIM,
            "content_hash": content_hash,
            "section_hierarchy": section_hierarchy,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    return processed_articles
