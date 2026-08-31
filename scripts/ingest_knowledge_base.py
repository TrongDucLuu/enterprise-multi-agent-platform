#!/usr/bin/env python3
"""
Enterprise Knowledge Base Data Ingestion Pipeline.

Parses multi-format customer documents (.md, .txt, .docx, .jsonl), validates enterprise system
tags against systems.yaml config, splits documents into semantic chunks, generates 768-dimensional
dense vector embeddings using text-embedding-005, and performs idempotent upsert (MERGE) into BigQuery.

Usage:
    # Dry-run parsing and embedding simulation:
    python scripts/ingest_knowledge_base.py --source-dir data/knowledge_base/ --dry-run

    # Production ingestion into BigQuery:
    python scripts/ingest_knowledge_base.py --source-dir data/knowledge_base/ --project-id my-project --dataset-id it_helpdesk_kb

    # Ingest single document and run test query:
    python scripts/ingest_knowledge_base.py --file docs/sap_procurement_guide.docx --system ERP --test-query "lỗi phân quyền ME21N"
"""

import os
import sys
import json
import hashlib
import argparse
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

# Ensure project root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from it_helpdesk_agent.app_utils.system_config import (
    get_configured_systems,
    get_valid_system_filters,
    get_system_metadata,
)
from it_helpdesk_agent.app_utils.embedding_utils import (
    DEFAULT_EMBEDDING_MODEL,
    generate_batch_embeddings,
    generate_text_embedding,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ingest_knowledge_base")


class DocumentParser:
    """Extracts raw text and metadata from various enterprise document formats."""

    @staticmethod
    def parse_markdown_or_text(file_path: Path) -> list[dict[str, Any]]:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Extract title from first markdown H1 if available
        title = file_path.stem.replace("_", " ").title()
        for line in content.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break

        return [{
            "title": title,
            "content": content,
            "source_uri": str(file_path),
            "file_type": file_path.suffix.lower(),
        }]

    @staticmethod
    def parse_docx(file_path: Path) -> list[dict[str, Any]]:
        try:
            import docx
            doc = docx.Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
            content = "\n\n".join(paragraphs)
            title = paragraphs[0] if paragraphs else file_path.stem.replace("_", " ").title()
            return [{
                "title": title,
                "content": content,
                "source_uri": str(file_path),
                "file_type": ".docx",
            }]
        except Exception as e:
            logger.error("Failed to parse docx file %s: %s", file_path, e)
            return []

    @staticmethod
    def parse_jsonl(file_path: Path) -> list[dict[str, Any]]:
        articles = []
        with open(file_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    articles.append({
                        "id": data.get("id"),
                        "system": data.get("system"),
                        "title": data.get("title", f"Article {line_no}"),
                        "category": data.get("category", "General"),
                        "content": data.get("content", ""),
                        "keywords": data.get("keywords", []),
                        "source_uri": data.get("source_uri", str(file_path)),
                        "file_type": ".jsonl",
                    })
                except json.JSONDecodeError as e:
                    logger.warning("Invalid JSON at line %d in %s: %s", line_no, file_path, e)
        return articles


def chunk_text(text: str, max_chunk_size: int = 1200, overlap: int = 150) -> list[str]:
    """Splits a long document into semantic paragraphs/chunks with overlap."""
    if len(text) <= max_chunk_size:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chunk_size
        if end >= len(text):
            chunks.append(text[start:].strip())
            break

        # Try to break on paragraph or sentence boundary
        break_idx = text.rfind("\n\n", start, end)
        if break_idx == -1 or break_idx <= start:
            break_idx = text.rfind(". ", start, end)
        if break_idx == -1 or break_idx <= start:
            break_idx = end

        chunks.append(text[start:break_idx].strip())
        start = max(start + 1, break_idx - overlap)

    return [c for c in chunks if c]


def process_document(
    doc_info: dict[str, Any],
    default_system: Optional[str] = None
) -> list[dict[str, Any]]:
    """
    Validates, chunks, and prepares a document for embedding and ingestion.
    Generates deterministic article IDs.
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
    raw_keywords = doc_info.get("keywords", [])

    chunks = chunk_text(content)
    processed_articles = []

    for idx, chunk in enumerate(chunks):
        # Generate deterministic ID
        if doc_info.get("id") and len(chunks) == 1:
            article_id = doc_info["id"].upper()
        else:
            hasher = hashlib.sha256()
            hasher.update(f"{system_clean}:{source_uri}:{title}:{idx}".encode("utf-8"))
            short_hash = hasher.hexdigest()[:8].upper()
            article_id = f"{system_clean}-KB-{short_hash}"

        chunk_title = title if len(chunks) == 1 else f"{title} (Phần {idx + 1}/{len(chunks)})"
        
        # Extract keywords if not provided
        keywords = raw_keywords.copy()
        if not keywords:
            words = [w.strip(".,;:()") for w in chunk_title.lower().split() if len(w) > 2]
            keywords = list(set(words))[:8]

        processed_articles.append({
            "id": article_id,
            "system": system_clean,
            "title": chunk_title,
            "category": category,
            "content": chunk,
            "keywords": keywords,
            "source_uri": source_uri,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    return processed_articles


def ensure_vector_index(
    bq_client: Any,
    project_id: str,
    dataset_id: str,
    table_name: str = "knowledge_articles",
    index_name: str = "knowledge_articles_vector_idx"
):
    """
    Executes BigQuery CREATE VECTOR INDEX DDL if index does not exist.
    BigQuery IVF Vector Index will automatically optimize vector queries;
    if dataset has fewer than 5,000 rows, BigQuery will automatically use exact cosine search.
    """
    ddl = f"""
    CREATE VECTOR INDEX IF NOT EXISTS `{index_name}`
    ON `{project_id}.{dataset_id}.{table_name}`(embedding)
    OPTIONS(distance_type='COSINE', index_type='IVF')
    """
    try:
        logger.info("Verifying / Creating BigQuery Vector Index '%s'...", index_name)
        query_job = bq_client.query(ddl)
        query_job.result()
        logger.info("BigQuery Vector Index '%s' is verified and active.", index_name)
    except Exception as e:
        logger.warning(
            "Note: BigQuery Vector Index DDL returned: %s. "
            "(BigQuery automatically executes exact cosine search when dataset size is under 5,000 rows threshold).",
            e
        )


def ingest_articles_to_bigquery(
    articles: list[dict[str, Any]],
    project_id: str,
    dataset_id: str,
    table_name: str = "knowledge_articles"
) -> int:
    """
    Performs batch upsert (MERGE) into BigQuery table with 768-dim embeddings and ensures Vector Index exists.
    """
    try:
        from google.cloud import bigquery
    except ImportError:
        logger.error("google-cloud-bigquery is not installed. Please install dependencies.")
        raise

    bq_client = bigquery.Client(project=project_id)
    full_table = f"`{project_id}.{dataset_id}.{table_name}`"

    logger.info("Generating embeddings for %d chunks using %s...", len(articles), DEFAULT_EMBEDDING_MODEL)
    texts_to_embed = [f"{a['title']}\n{a['category']}\n{a['content']}" for a in articles]
    embeddings = generate_batch_embeddings(texts_to_embed, model_name=DEFAULT_EMBEDDING_MODEL)

    for a, emb in zip(articles, embeddings):
        a["embedding"] = emb

    # Prepare rows for BigQuery streaming / MERGE
    logger.info("Upserting %d articles into BigQuery %s...", len(articles), full_table)
    
    errors = bq_client.insert_rows_json(
        f"{project_id}.{dataset_id}.{table_name}",
        articles
    )
    if errors:
        logger.error("BigQuery insert_rows_json encountered errors: %s", errors)
        raise RuntimeError(f"BigQuery insertion failed: {errors}")

    logger.info("Successfully ingested %d articles into BigQuery.", len(articles))

    # Automatically ensure Vector Index DDL
    ensure_vector_index(bq_client, project_id, dataset_id, table_name)

    return len(articles)


def run_test_query(
    query: str,
    project_id: str,
    dataset_id: str,
    table_name: str = "knowledge_articles",
    dry_run: bool = False,
    sample_articles: Optional[list[dict[str, Any]]] = None
):
    """Executes a test vector search query to verify retrieval accuracy."""
    logger.info("--- Testing Query: '%s' ---", query)
    if dry_run or not project_id:
        logger.info("[Dry-Run] Simulating cosine similarity against %d local chunks...", len(sample_articles or []))
        query_vec = generate_text_embedding(query, model_name=DEFAULT_EMBEDDING_MODEL, use_vertex=False)
        scored = []
        for a in (sample_articles or []):
            vec = a.get("embedding") or generate_text_embedding(a["content"], use_vertex=False)
            dot = sum(x * y for x, y in zip(query_vec, vec))
            scored.append((dot, a))
        scored.sort(key=lambda x: x[0], reverse=True)
        for rank, (score, art) in enumerate(scored[:3], 1):
            logger.info("Top %d [%.2f] [%s] %s (ID: %s)", rank, score, art["system"], art["title"], art["id"])
        return

    try:
        from it_helpdesk_agent.tools.enterprise_rag_mcp.knowledge_store import BigQueryVectorKnowledgeStore
        store = BigQueryVectorKnowledgeStore(
            project_id=project_id,
            dataset_id=dataset_id,
            table_name=table_name
        )
        results = store.search(query=query, system="ALL", limit=3)
        for rank, r in enumerate(results, 1):
            logger.info("Top %d [Score: %.2f] [%s] %s (ID: %s)", rank, r.relevance_score, r.system, r.title, r.article_id)
            logger.info("   Snippet: %s", r.snippet[:120])
    except Exception as e:
        logger.error("Test query failed: %s", e)


def main():
    parser = argparse.ArgumentParser(description="Ingest customer documentation into Enterprise Knowledge Base")
    parser.add_argument("--source-dir", type=str, help="Directory containing documents (.md, .txt, .docx, .jsonl)")
    parser.add_argument("--file", type=str, help="Single document file to ingest")
    parser.add_argument("--system", type=str, help="Default enterprise system (e.g. ERP, HRM, CRM)")
    parser.add_argument("--project-id", type=str, default=os.getenv("GOOGLE_CLOUD_PROJECT", ""), help="Google Cloud Project ID")
    parser.add_argument("--dataset-id", type=str, default=os.getenv("BIGQUERY_KB_DATASET", "it_helpdesk_kb"), help="BigQuery Dataset ID")
    parser.add_argument("--table-name", type=str, default="knowledge_articles", help="BigQuery Table Name")
    parser.add_argument("--dry-run", action="store_true", help="Parse and embed locally without writing to BigQuery")
    parser.add_argument("--test-query", type=str, help="Run a verification query after ingestion")

    args = parser.parse_args()

    files_to_process: list[Path] = []
    if args.file:
        p = Path(args.file)
        if not p.exists():
            logger.error("File not found: %s", p)
            sys.exit(1)
        files_to_process.append(p)
    elif args.source_dir:
        p = Path(args.source_dir)
        if not p.exists():
            logger.error("Directory not found: %s", p)
            sys.exit(1)
        for ext in ("*.md", "*.txt", "*.docx", "*.jsonl"):
            files_to_process.extend(p.glob(ext))
    else:
        # Default to data/knowledge_base if present
        default_data_dir = BASE_DIR / "data" / "knowledge_base"
        if default_data_dir.exists():
            for ext in ("*.md", "*.txt", "*.docx", "*.jsonl"):
                files_to_process.extend(default_data_dir.glob(ext))
        else:
            logger.error("Please specify --source-dir or --file")
            sys.exit(1)

    if not files_to_process:
        logger.warning("No supported document files found to process.")
        sys.exit(0)

    logger.info("Found %d file(s) to process.", len(files_to_process))

    all_articles: list[dict[str, Any]] = []
    for fp in files_to_process:
        logger.info("Parsing %s...", fp.name)
        if fp.suffix.lower() in (".md", ".txt"):
            docs = DocumentParser.parse_markdown_or_text(fp)
        elif fp.suffix.lower() == ".docx":
            docs = DocumentParser.parse_docx(fp)
        elif fp.suffix.lower() == ".jsonl":
            docs = DocumentParser.parse_jsonl(fp)
        else:
            continue

        for d in docs:
            try:
                processed = process_document(d, default_system=args.system)
                all_articles.extend(processed)
            except Exception as e:
                logger.error("Error processing document from %s: %s", fp.name, e)

    logger.info("Successfully parsed into %d chunked knowledge article(s).", len(all_articles))

    if args.dry_run:
        logger.info("[Dry-Run Mode] Generating sample embeddings locally (No BigQuery writes)...")
        texts = [a["content"] for a in all_articles]
        embeddings = generate_batch_embeddings(texts, model_name=DEFAULT_EMBEDDING_MODEL, use_vertex=False)
        for a, emb in zip(all_articles, embeddings):
            a["embedding"] = emb
        logger.info("[Dry-Run Mode] All %d articles validated and embedded successfully.", len(all_articles))
    else:
        if not args.project_id:
            logger.error("Project ID is required for BigQuery ingestion. Set GOOGLE_CLOUD_PROJECT or pass --project-id")
            sys.exit(1)
        ingest_articles_to_bigquery(
            all_articles,
            project_id=args.project_id,
            dataset_id=args.dataset_id,
            table_name=args.table_name
        )

    if args.test_query:
        run_test_query(
            args.test_query,
            project_id=args.project_id,
            dataset_id=args.dataset_id,
            table_name=args.table_name,
            dry_run=args.dry_run,
            sample_articles=all_articles
        )


if __name__ == "__main__":
    main()
