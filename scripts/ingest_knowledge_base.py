#!/usr/bin/env python3
"""
Enterprise Knowledge Base Data Ingestion Pipeline.

Parses multi-format customer documents (.md, .txt, .docx, .pdf, .jsonl), validates enterprise system
tags against systems.yaml config, splits documents into semantic chunks, generates 768-dimensional
dense vector embeddings using text-embedding-005, and performs idempotent upsert (MERGE) with
orphaned chunks cleanup via temporary Staging Table into BigQuery.

Usage:
    # Dry-run parsing and embedding simulation:
    python scripts/ingest_knowledge_base.py --source-dir data/knowledge_base/ --dry-run

    # Production ingestion into BigQuery:
    python scripts/ingest_knowledge_base.py --source-dir data/knowledge_base/ --project-id my-project --dataset-id it_helpdesk_kb

    # Ingest single document and run test query:
    python scripts/ingest_knowledge_base.py --file docs/sap_procurement_guide.pdf --system ERP --test-query "lỗi phân quyền ME21N"
"""

import os
import sys
import json
import uuid
import hashlib
import argparse
import logging
from datetime import datetime, timezone, timedelta
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
        except ImportError:
            logger.warning("python-docx is not installed. To parse DOCX files, install python-docx (`pip install python-docx`).")
            return []
        except Exception as e:
            logger.error("Failed to parse docx file %s: %s", file_path, e)
            return []

    @staticmethod
    def parse_pdf(file_path: Path) -> list[dict[str, Any]]:
        try:
            import pypdf
            reader = pypdf.PdfReader(str(file_path))
            pages_text = []
            for i, page in enumerate(reader.pages):
                text = page.extract_text() or ""
                if text.strip():
                    pages_text.append(text.strip())
            content = "\n\n".join(pages_text)
            title = file_path.stem.replace("_", " ").title()
            if pages_text:
                first_lines = [l.strip() for l in pages_text[0].splitlines() if l.strip()]
                if first_lines:
                    title = first_lines[0][:100]
            return [{
                "title": title,
                "content": content,
                "source_uri": str(file_path),
                "file_type": ".pdf",
            }]
        except ImportError:
            logger.warning("pypdf is not installed. To parse PDF files, install pypdf (`pip install pypdf`).")
            return []
        except Exception as e:
            logger.error("Failed to parse pdf file %s: %s", file_path, e)
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
    Generates deterministic article IDs based on SHA-256(system:source_uri:title:idx)
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
            "content_hash": content_hash,
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
    Performs production-grade idempotent upsert (MERGE) into BigQuery:
    1. CDC pre-check on content_hash to skip redundant embedding API calls.
    2. Batch loads articles into a temporary staging table (zero streaming buffer locks on target).
    3. Executes atomic SQL MERGE from staging table into target table.
    4. Executes DML DELETE on target table to clean up orphaned chunks for modified documents.
    5. Drops staging table and ensures BigQuery IVF Vector Index is active.
    """
    if not articles:
        logger.info("No articles to ingest.")
        return 0

    try:
        from google.cloud import bigquery
    except ImportError:
        logger.error("google-cloud-bigquery is not installed. Please install dependencies.")
        raise

    bq_client = bigquery.Client(project=project_id)
    full_target_table = f"`{project_id}.{dataset_id}.{table_name}`"

    # 1. CDC Pre-Check: Retrieve existing content hashes to avoid redundant embedding generation
    source_uris = list({a["source_uri"] for a in articles if a.get("source_uri")})
    existing_hashes: dict[str, str] = {}
    existing_embeddings: dict[str, list[float]] = {}

    if source_uris:
        try:
            cdc_sql = f"""
            SELECT id, content_hash, embedding
            FROM {full_target_table}
            WHERE source_uri IN UNNEST(@source_uris)
            """
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("source_uris", "STRING", source_uris)
                ]
            )
            cdc_rows = list(bq_client.query(cdc_sql, job_config=job_config).result())
            for r in cdc_rows:
                if hasattr(r, "id") and hasattr(r, "content_hash") and r.id and r.content_hash:
                    existing_hashes[r.id] = r.content_hash
                    if hasattr(r, "embedding") and r.embedding:
                        existing_embeddings[r.id] = list(r.embedding)
        except Exception as e:
            logger.debug("CDC pre-check bypassed (table may be newly initialized): %s", e)

    # 2. Selective Embedding Generation
    chunks_to_embed_indices: list[int] = []
    texts_to_embed: list[str] = []

    for idx, a in enumerate(articles):
        art_id = a.get("id")
        content_hash = a.get("content_hash")
        # Reuse existing vector if content is unchanged
        if art_id and art_id in existing_hashes and existing_hashes[art_id] == content_hash and art_id in existing_embeddings:
            a["embedding"] = existing_embeddings[art_id]
        elif a.get("embedding"):
            # Already has embedding (e.g. injected or dry-run)
            pass
        else:
            chunks_to_embed_indices.append(idx)
            texts_to_embed.append(f"{a['title']}\n{a['category']}\n{a['content']}")

    reused_count = len(articles) - len(chunks_to_embed_indices)
    if reused_count > 0:
        logger.info("CDC Optimization: Reused %d existing embeddings (content unchanged).", reused_count)

    if chunks_to_embed_indices:
        logger.info("Generating embeddings for %d new/modified chunks using %s...", len(chunks_to_embed_indices), DEFAULT_EMBEDDING_MODEL)
        new_embeddings = generate_batch_embeddings(texts_to_embed, model_name=DEFAULT_EMBEDDING_MODEL)
        for idx, emb in zip(chunks_to_embed_indices, new_embeddings):
            articles[idx]["embedding"] = emb

    # 3. Create Temporary Staging Table
    staging_suffix = uuid.uuid4().hex[:8]
    staging_table_name = f"{table_name}_staging_{staging_suffix}"
    staging_table_id = f"{project_id}.{dataset_id}.{staging_table_name}"
    full_staging_table = f"`{project_id}.{dataset_id}.{staging_table_name}`"

    schema = [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("system", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("title", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("category", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("content", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("keywords", "STRING", mode="REPEATED"),
        bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
        bigquery.SchemaField("source_uri", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("content_hash", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
    ]

    staging_table = bigquery.Table(staging_table_id, schema=schema)
    staging_table.expires = datetime.now(timezone.utc) + timedelta(hours=1)
    bq_client.create_table(staging_table, exists_ok=True)
    logger.info("Created temporary staging table %s", staging_table_name)

    try:
        # 4. Batch Load Articles into Staging Table (Load Job - Free of streaming buffer locks)
        load_job_config = bigquery.LoadJobConfig(
            schema=schema,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        )
        load_job = bq_client.load_table_from_json(articles, staging_table_id, job_config=load_job_config)
        load_job.result()
        logger.info("Loaded %d articles into staging table.", len(articles))

        # 5. Execute Atomic SQL MERGE from Staging into Target Table
        merge_sql = f"""
        MERGE {full_target_table} T
        USING {full_staging_table} S
        ON T.id = S.id
        WHEN MATCHED AND (T.content_hash != S.content_hash OR T.content_hash IS NULL) THEN
          UPDATE SET
            T.system = S.system,
            T.title = S.title,
            T.category = S.category,
            T.content = S.content,
            T.keywords = S.keywords,
            T.embedding = S.embedding,
            T.source_uri = S.source_uri,
            T.content_hash = S.content_hash,
            T.updated_at = S.updated_at
        WHEN NOT MATCHED THEN
          INSERT (id, system, title, category, content, keywords, embedding, source_uri, content_hash, updated_at)
          VALUES (S.id, S.system, S.title, S.category, S.content, S.keywords, S.embedding, S.source_uri, S.content_hash, S.updated_at);
        """
        logger.info("Executing Atomic MERGE into %s...", full_target_table)
        merge_job = bq_client.query(merge_sql)
        merge_job.result()
        logger.info("Atomic MERGE completed successfully.")

        # 6. Execute Orphaned Chunks Cleanup via DML on Target Table (No streaming buffer lock on target!)
        if source_uris:
            cleanup_sql = f"""
            DELETE FROM {full_target_table}
            WHERE source_uri IN UNNEST(@source_uris)
              AND id NOT IN (
                SELECT id FROM {full_staging_table}
              )
            """
            cleanup_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ArrayQueryParameter("source_uris", "STRING", source_uris)
                ]
            )
            cleanup_job = bq_client.query(cleanup_sql, job_config=cleanup_config)
            cleanup_job.result()
            deleted_count = getattr(cleanup_job, "num_dml_affected_rows", 0)
            if isinstance(deleted_count, (int, float)) and deleted_count > 0:
                logger.info("Cleaned up %d orphaned chunks for updated documents.", int(deleted_count))

    finally:
        # 7. Drop Temporary Staging Table
        try:
            bq_client.delete_table(staging_table_id, not_found_ok=True)
            logger.info("Cleaned up temporary staging table %s", staging_table_name)
        except Exception as e:
            logger.warning("Failed to drop staging table %s: %s", staging_table_name, e)

    # 8. Automatically Ensure Vector Index DDL
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
    parser.add_argument("--source-dir", type=str, help="Directory containing documents (.md, .txt, .docx, .pdf, .jsonl)")
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
        for ext in ("*.md", "*.txt", "*.docx", "*.pdf", "*.jsonl"):
            files_to_process.extend(p.glob(ext))
    else:
        # Default to data/knowledge_base if present
        default_data_dir = BASE_DIR / "data" / "knowledge_base"
        if default_data_dir.exists():
            for ext in ("*.md", "*.txt", "*.docx", "*.pdf", "*.jsonl"):
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
        elif fp.suffix.lower() == ".pdf":
            docs = DocumentParser.parse_pdf(fp)
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
