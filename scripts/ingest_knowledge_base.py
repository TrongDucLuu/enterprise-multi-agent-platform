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
import time
import argparse
import logging
from pathlib import Path
from typing import Optional, Any

# Ensure project root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.ingest import (
    DocumentParser,
    PARSER_VERSION,
    is_well_structured,
    chunk_by_sections,
    chunk_text,
    process_document,
    CHUNKER_VERSION,
    generate_batch_embeddings,
    generate_text_embedding,
    DEFAULT_EMBEDDING_MODEL,
    EMBEDDING_MODEL,
    EMBEDDING_DIM,
    ensure_vector_index,
    check_vector_index_coverage,
    ingest_articles_to_bigquery,
    reconcile_deleted_documents,
    purge_tombstoned_chunks,
    get_stale_chunks_for_reprocessing,
    run_test_query,
)

__all__ = [
    "DocumentParser",
    "PARSER_VERSION",
    "is_well_structured",
    "chunk_by_sections",
    "chunk_text",
    "process_document",
    "CHUNKER_VERSION",
    "generate_batch_embeddings",
    "generate_text_embedding",
    "DEFAULT_EMBEDDING_MODEL",
    "EMBEDDING_MODEL",
    "EMBEDDING_DIM",
    "ensure_vector_index",
    "check_vector_index_coverage",
    "ingest_articles_to_bigquery",
    "reconcile_deleted_documents",
    "purge_tombstoned_chunks",
    "get_stale_chunks_for_reprocessing",
    "run_test_query",
    "main",
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ingest_knowledge_base")


def main():
    start_time = time.time()
    parser = argparse.ArgumentParser(description="Ingest customer documentation into Enterprise Knowledge Base")
    parser.add_argument("--source-dir", type=str, help="Directory containing documents (.md, .txt, .docx, .pdf, .jsonl)")
    parser.add_argument("--file", type=str, help="Single document file to ingest")
    parser.add_argument("--system", type=str, help="Default enterprise system (e.g. ERP, HRM, CRM)")
    parser.add_argument("--project-id", type=str, default=os.getenv("GOOGLE_CLOUD_PROJECT", ""), help="Google Cloud Project ID")
    parser.add_argument("--dataset-id", type=str, default=os.getenv("BIGQUERY_KB_DATASET", "it_helpdesk_kb"), help="BigQuery Dataset ID")
    parser.add_argument("--table-name", type=str, default="knowledge_articles", help="BigQuery Table Name")
    parser.add_argument("--dry-run", action="store_true", help="Parse and embed locally without writing to BigQuery")
    parser.add_argument("--reconcile", action="store_true", help="Tombstone documents missing from source directory")
    parser.add_argument("--purge-tombstones-older-than", type=int, help="Hard delete tombstones older than N days")
    parser.add_argument("--reprocess-where", type=str, help="Check and list stale chunks requiring reprocessing")
    parser.add_argument("--test-query", type=str, help="Run a verification query after ingestion")

    args = parser.parse_args()

    # Maintenance / Query options
    if args.purge_tombstones_older_than is not None:
        if not args.project_id:
            logger.error("Project ID is required for tombstone purge.")
            sys.exit(1)
        from google.cloud import bigquery
        bq_client = bigquery.Client(project=args.project_id)
        purge_tombstoned_chunks(bq_client, args.project_id, args.dataset_id, args.table_name, older_than_days=args.purge_tombstones_older_than)
        return

    if args.reprocess_where:
        if not args.project_id:
            logger.error("Project ID is required to query stale chunks.")
            sys.exit(1)
        from google.cloud import bigquery
        bq_client = bigquery.Client(project=args.project_id)
        stale = get_stale_chunks_for_reprocessing(
            bq_client, args.project_id, args.dataset_id, args.table_name,
            current_chunker_version=CHUNKER_VERSION, current_parser_version=PARSER_VERSION
        )
        logger.info("Found %d stale chunks matching reprocessing criteria.", len(stale))
        return

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

    logger.info("Found %d file(s) to process. Pipeline Versions: Parser=%s, Chunker=%s, Embedder=%s (%d dim)",
                len(files_to_process), PARSER_VERSION, CHUNKER_VERSION, EMBEDDING_MODEL, EMBEDDING_DIM)

    all_articles: list[dict[str, Any]] = []
    dead_letter_queue: list[dict[str, Any]] = []

    for fp in files_to_process:
        logger.info("Parsing %s...", fp.name)
        try:
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
                    logger.error("Dead Letter Queue (DLQ): Failed processing doc '%s' from %s: %s", d.get("title"), fp.name, e)
                    dead_letter_queue.append({"file": str(fp), "doc": d, "error": str(e), "stage": "chunking"})
        except Exception as e:
            logger.error("Dead Letter Queue (DLQ): Failed parsing file %s: %s", fp.name, e)
            dead_letter_queue.append({"file": str(fp), "error": str(e), "stage": "parsing"})

    logger.info("Successfully parsed into %d chunked knowledge article(s) (Failed docs in DLQ: %d).",
                len(all_articles), len(dead_letter_queue))

    if dead_letter_queue:
        logger.warning("Dead Letter Queue Summary: %d document(s) failed during ingestion pipeline.", len(dead_letter_queue))

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

        # Reconciliation: if --reconcile or full directory sync
        if args.reconcile or (args.source_dir and not args.file):
            active_uris = list({str(fp) for fp in files_to_process})
            for a in all_articles:
                if a.get("source_uri"):
                    active_uris.append(a["source_uri"])
            active_uris = list(set(active_uris))
            
            from google.cloud import bigquery
            bq_client = bigquery.Client(project=args.project_id)
            reconcile_deleted_documents(
                bq_client=bq_client,
                project_id=args.project_id,
                dataset_id=args.dataset_id,
                table_name=args.table_name,
                active_source_uris=active_uris
            )

    ingestion_lag_seconds = time.time() - start_time
    logger.info("Ingestion Job Completed. Ingestion Lag: %.2f seconds. Total chunks: %d, Failed docs: %d",
                ingestion_lag_seconds, len(all_articles), len(dead_letter_queue))

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
