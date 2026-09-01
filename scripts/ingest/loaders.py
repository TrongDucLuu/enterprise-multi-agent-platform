"""
BigQuery Loaders and Vector Index Management for Enterprise Knowledge Base.
Provides idempotent upsert (atomic MERGE), staging table orchestration, orphaned chunk cleanup,
vector index DDL creation, and coverage monitoring.
"""

import os
import json
import uuid
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional, Any

from scripts.ingest.embedders import (
    DEFAULT_EMBEDDING_MODEL,
    generate_batch_embeddings,
    generate_text_embedding,
)

logger = logging.getLogger("ingest.loaders")


def ensure_vector_index(
    bq_client: Any,
    project_id: str,
    dataset_id: str,
    table_name: str = "knowledge_articles",
    index_name: str = "knowledge_articles_vector_idx"
):
    """
    Executes BigQuery CREATE VECTOR INDEX DDL if index does not exist with STORING clause.
    BigQuery IVF Vector Index will automatically optimize vector queries;
    if dataset has fewer than 5,000 rows, BigQuery will automatically use exact cosine search.
    """
    ddl = f"""
    CREATE VECTOR INDEX IF NOT EXISTS `{index_name}`
    ON `{project_id}.{dataset_id}.{table_name}`(embedding)
    STORING (system, category, id, title, content, section_h1, section_h2, section_h3, source_uri, owner, effective_date, expiry_date, is_deleted, parent_doc_id, chunk_index, allowed_roles, sensitivity, keywords, clearance_level)
    OPTIONS(distance_type='COSINE', index_type='IVF', lexical_search_columns=['title', 'content', 'keywords'])
    """
    try:
        logger.info("Verifying / Creating BigQuery Vector Index '%s' with STORING columns...", index_name)
        query_job = bq_client.query(ddl)
        query_job.result()
        logger.info("BigQuery Vector Index '%s' is verified and active.", index_name)
    except Exception as e:
        logger.warning(
            "Note: BigQuery Vector Index DDL returned: %s. "
            "(BigQuery automatically executes exact cosine search when dataset size is under 5,000 rows threshold).",
            e
        )


def check_vector_index_coverage(
    bq_client: Any,
    project_id: str,
    dataset_id: str,
    table_name: str = "knowledge_articles",
    index_name: str = "knowledge_articles_vector_idx"
) -> dict[str, Any]:
    """
    Monitors BigQuery Vector Index status, coverage percentage, and unindexed row count via INFORMATION_SCHEMA.
    Logs clear operational diagnostics for enterprise observability.
    """
    coverage_sql = f"""
    SELECT 
        table_name,
        index_name,
        index_status,
        coverage_percentage,
        unindexed_row_count,
        total_row_count
    FROM `{project_id}.{dataset_id}.INFORMATION_SCHEMA.VECTOR_INDEXES`
    WHERE table_name = @table_name AND index_name = @index_name
    """
    try:
        from google.cloud import bigquery
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("table_name", "STRING", table_name),
                bigquery.ScalarQueryParameter("index_name", "STRING", index_name),
            ]
        )
        rows = list(bq_client.query(coverage_sql, job_config=job_config).result())
        if not rows:
            logger.info("Vector Index '%s' does not exist yet in INFORMATION_SCHEMA or is newly scheduled.", index_name)
            return {"index_status": "NOT_FOUND", "coverage_percentage": 0.0}

        row = rows[0]
        status = getattr(row, "index_status", "UNKNOWN")
        coverage = getattr(row, "coverage_percentage", 0.0) or 0.0
        unindexed = getattr(row, "unindexed_row_count", 0) or 0
        total = getattr(row, "total_row_count", 0) or 0

        if coverage == 0.0:
            if status == "TEMPORARILY DISABLED":
                logger.info(
                    "Lưu ý: Vector Index '%s' có trạng thái 'TEMPORARILY DISABLED' (Coverage: 0.0%%). "
                    "Nguyên nhân: Kích thước bảng tri thức dưới ngưỡng tối thiểu (thường < 10 MB) "
                    "nên BigQuery tự động dùng Exact Cosine Search. Đây là hành vi bình thường cho cơ sở tri thức nhỏ của khách hàng mới.",
                    index_name
                )
            else:
                logger.warning(
                    "CẢNH BÁO: Vector Index '%s' coverage = 0.0%% (Status: %s). "
                    "Truy vấn sẽ thực hiện Full Table Scan cho tới khi index hoàn tất indexing.",
                    index_name, status
                )
        else:
            logger.info(
                "Vector Index '%s' đang hoạt động tốt. Status: %s, Coverage: %.1f%%, Dòng chưa index: %d / %d tổng số dòng.",
                index_name, status, coverage, unindexed, total
            )

        return {
            "index_status": status,
            "coverage_percentage": coverage,
            "unindexed_row_count": unindexed,
            "total_row_count": total,
        }
    except Exception as e:
        logger.warning("Could not query INFORMATION_SCHEMA.VECTOR_INDEXES: %s", e)
def get_knowledge_articles_schema() -> list[Any]:
    """Returns the canonical BigQuery SchemaField list for enterprise knowledge articles."""
    from google.cloud import bigquery
    return [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("parent_doc_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("chunk_index", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("system", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("title", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("category", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("content", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("keywords", "STRING", mode="REPEATED"),
        bigquery.SchemaField("embedding", "FLOAT64", mode="REPEATED"),
        bigquery.SchemaField("section_h1", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("section_h2", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("section_h3", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("allowed_roles", "STRING", mode="REPEATED"),
        bigquery.SchemaField("sensitivity", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("clearance_level", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("source_uri", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("owner", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("effective_date", "DATE", mode="NULLABLE"),
        bigquery.SchemaField("expiry_date", "DATE", mode="NULLABLE"),
        bigquery.SchemaField("is_deleted", "BOOLEAN", mode="NULLABLE"),
        bigquery.SchemaField("deleted_at", "TIMESTAMP", mode="NULLABLE"),
        bigquery.SchemaField("parser_version", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("chunker_version", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("embedding_model", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("embedding_dim", "INTEGER", mode="NULLABLE"),
        bigquery.SchemaField("content_hash", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("updated_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def get_dlq_schema() -> list[Any]:
    """Returns the canonical BigQuery SchemaField list for ingestion dead-letter queue (DLQ)."""
    from google.cloud import bigquery
    return [
        bigquery.SchemaField("id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("file_path", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("stage", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("error_message", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("doc_title", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("doc_payload", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("occurred_at", "TIMESTAMP", mode="REQUIRED"),
    ]


def persist_dead_letter_queue(
    dead_letter_queue: list[dict[str, Any]],
    project_id: Optional[str] = None,
    dataset_id: Optional[str] = None,
    table_name: str = "ingestion_dead_letter_queue",
    dlq_file_path: Optional[str] = None,
    bq_client: Optional[Any] = None,
) -> int:
    """
    Persists unparseable/failed documents from the Dead-Letter Queue (DLQ) to durable storage:
    1. Writes JSONL records to local/persistent file storage (e.g. data/dlq/ingestion_dlq.jsonl).
    2. If project_id & dataset_id are supplied, streams/inserts records into BigQuery DLQ table.
    3. Emits structured CRITICAL / ALERT logs formatted for Google Cloud Monitoring log-based alert metric triggers.
    """
    if not dead_letter_queue:
        return 0

    records: list[dict[str, Any]] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for item in dead_letter_queue:
        doc_info = item.get("doc") or {}
        doc_title = doc_info.get("title") if isinstance(doc_info, dict) else None
        doc_payload_str = json.dumps(doc_info, ensure_ascii=False) if doc_info else None
        rec = {
            "id": str(uuid.uuid4()),
            "file_path": str(item.get("file", "unknown")),
            "stage": str(item.get("stage", "unknown")),
            "error_message": str(item.get("error", "unknown error")),
            "doc_title": doc_title,
            "doc_payload": doc_payload_str,
            "occurred_at": now_iso,
        }
        records.append(rec)

    # 1. File Persistence (Fallback & Local Audit)
    target_path = Path(dlq_file_path or os.getenv("DLQ_STORAGE_PATH", "data/dlq/ingestion_dlq.jsonl"))
    try:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with open(target_path, "a", encoding="utf-8") as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        logger.info("Persisted %d DLQ failure record(s) to %s", len(records), target_path)
    except Exception as e:
        logger.error("Failed persisting DLQ to file %s: %s", target_path, e)

    # 2. BigQuery Persistence (if GCP credentials/project supplied)
    if project_id and dataset_id:
        try:
            from google.cloud import bigquery
            client = bq_client or bigquery.Client(project=project_id)
            full_table_id = f"{project_id}.{dataset_id}.{table_name}"
            
            # Insert rows into BigQuery DLQ table
            errors = client.insert_rows_json(full_table_id, records)
            if errors:
                logger.error("BigQuery insert_rows_json DLQ errors: %s", errors)
            else:
                logger.info("Successfully persisted %d DLQ record(s) to BigQuery table %s", len(records), full_table_id)
        except Exception as e:
            logger.warning("Could not persist DLQ to BigQuery table `%s.%s.%s`: %s", project_id, dataset_id, table_name, e)

    # 3. Active Alerting trigger for Cloud Monitoring
    logger.critical(
        "ALERT: DLQ_THRESHOLD_EXCEEDED — %d document(s) failed ingestion! "
        "Alert details: %s",
        len(records),
        json.dumps([{"file": r["file_path"], "stage": r["stage"], "error": r["error_message"][:100]} for r in records], ensure_ascii=False)
    )

    return len(records)


def read_persisted_dead_letter_queue(
    dlq_file_path: Optional[str] = None,
    project_id: Optional[str] = None,
    dataset_id: Optional[str] = None,
    table_name: str = "ingestion_dead_letter_queue",
    bq_client: Optional[Any] = None,
) -> list[dict[str, Any]]:
    """
    Reads back persisted DLQ records from persistent storage (file or BigQuery).
    Allows human operators and automated health watchdogs to audit ingestion failures.
    """
    results: list[dict[str, Any]] = []

    # Try file storage
    target_path = Path(dlq_file_path or os.getenv("DLQ_STORAGE_PATH", "data/dlq/ingestion_dlq.jsonl"))
    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        results.append(json.loads(line))
        except Exception as e:
            logger.warning("Could not read DLQ records from %s: %s", target_path, e)

    # If project_id & dataset_id given and no file entries or requested, also check BigQuery
    if not results and project_id and dataset_id:
        try:
            from google.cloud import bigquery
            client = bq_client or bigquery.Client(project=project_id)
            query = f"SELECT id, file_path, stage, error_message, doc_title, doc_payload, occurred_at FROM `{project_id}.{dataset_id}.{table_name}` ORDER BY occurred_at DESC LIMIT 100"
            rows = list(client.query(query).result())
            results = [dict(r) for r in rows]
        except Exception as e:
            logger.warning("Could not query DLQ records from BigQuery: %s", e)

    return results


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
    3. Executes atomic SQL MERGE from staging table into target table with section_hierarchy.
    4. Executes DML DELETE on target table to clean up orphaned chunks for modified documents.
    5. Drops staging table, ensures BigQuery IVF Vector Index with STORING is active, and monitors coverage.
    """
    if not articles:
        logger.info("No articles to ingest.")
        return 0

    # Deduplicate input articles by 'id', keeping the latest entry
    deduped_articles: dict[str, dict[str, Any]] = {}
    duplicate_id_count = 0
    duplicate_ids_sample: list[str] = []
    for a in articles:
        art_id = a.get("id")
        if art_id:
            if art_id in deduped_articles:
                duplicate_id_count += 1
                if len(duplicate_ids_sample) < 5:
                    duplicate_ids_sample.append(art_id)
            deduped_articles[art_id] = a
        else:
            deduped_articles[str(uuid.uuid4())] = a

    if duplicate_id_count > 0:
        logger.warning(
            "Phát hiện %d chunk trùng ID trong tập nạp (ví dụ các ID: %s). "
            "Hệ thống đã tự động loại bỏ bản ghi cũ và giữ bản ghi mới nhất để bảo vệ an toàn cho câu lệnh MERGE.",
            duplicate_id_count,
            ", ".join(duplicate_ids_sample)
        )
    articles = list(deduped_articles.values())

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

    # Ensure clearance_level is populated for all articles
    for a in articles:
        if a.get("clearance_level") is None:
            sens = (a.get("sensitivity") or "INTERNAL").upper()
            if sens == "PUBLIC":
                a["clearance_level"] = 0
            elif sens == "CONFIDENTIAL":
                a["clearance_level"] = 2
            elif sens == "RESTRICTED":
                a["clearance_level"] = 3
            else:
                a["clearance_level"] = 1

    # 3. Create Temporary Staging Table
    staging_suffix = uuid.uuid4().hex[:8]
    staging_table_name = f"{table_name}_staging_{staging_suffix}"
    staging_table_id = f"{project_id}.{dataset_id}.{staging_table_name}"
    full_staging_table = f"`{project_id}.{dataset_id}.{staging_table_name}`"

    schema = get_knowledge_articles_schema()

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

        # 5. Execute Atomic SQL MERGE from Staging into Target Table with deduplication fail-safe
        merge_sql = f"""
        MERGE {full_target_table} T
        USING (
          SELECT * FROM {full_staging_table}
          QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_at DESC) = 1
        ) S
        ON T.id = S.id
        WHEN MATCHED AND (T.content_hash != S.content_hash OR T.content_hash IS NULL) THEN
          UPDATE SET
            T.parent_doc_id = S.parent_doc_id,
            T.chunk_index = S.chunk_index,
            T.system = S.system,
            T.title = S.title,
            T.category = S.category,
            T.content = S.content,
            T.keywords = S.keywords,
            T.embedding = S.embedding,
            T.section_h1 = S.section_h1,
            T.section_h2 = S.section_h2,
            T.section_h3 = S.section_h3,
            T.allowed_roles = S.allowed_roles,
            T.sensitivity = S.sensitivity,
            T.clearance_level = S.clearance_level,
            T.source_uri = S.source_uri,
            T.owner = S.owner,
            T.effective_date = S.effective_date,
            T.expiry_date = S.expiry_date,
            T.is_deleted = S.is_deleted,
            T.deleted_at = S.deleted_at,
            T.parser_version = S.parser_version,
            T.chunker_version = S.chunker_version,
            T.embedding_model = S.embedding_model,
            T.embedding_dim = S.embedding_dim,
            T.content_hash = S.content_hash,
            T.updated_at = S.updated_at
        WHEN NOT MATCHED THEN
          INSERT (
            id, parent_doc_id, chunk_index, system, title, category, content, keywords, embedding,
            section_h1, section_h2, section_h3, allowed_roles, sensitivity, clearance_level, source_uri,
            owner, effective_date, expiry_date, is_deleted, deleted_at,
            parser_version, chunker_version, embedding_model, embedding_dim,
            content_hash, updated_at
          )
          VALUES (
            S.id, S.parent_doc_id, S.chunk_index, S.system, S.title, S.category, S.content, S.keywords, S.embedding,
            S.section_h1, S.section_h2, S.section_h3, S.allowed_roles, S.sensitivity, S.clearance_level, S.source_uri,
            S.owner, S.effective_date, S.expiry_date, S.is_deleted, S.deleted_at,
            S.parser_version, S.chunker_version, S.embedding_model, S.embedding_dim,
            S.content_hash, S.updated_at
          );
        """
        logger.info("Executing Atomic MERGE into %s...", full_target_table)
        merge_job = bq_client.query(merge_sql)
        merge_job.result()
        logger.info("Atomic MERGE completed successfully.")

        # 6. Execute Orphaned Chunks Cleanup via DML on Target Table
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

        # Invalidate semantic cache for updated/inserted articles
        try:
            from agent_core.app_utils.semantic_cache import get_semantic_cache
            cache = get_semantic_cache()
            updated_doc_ids = set()
            for art in articles:
                parent_id = art.get("parent_doc_id") or art.get("id")
                sys_name = art.get("system")
                if parent_id:
                    updated_doc_ids.add((parent_id, sys_name))
            for parent_id, sys_name in updated_doc_ids:
                cache.invalidate(article_id=parent_id, system=sys_name)
            if updated_doc_ids:
                logger.info("Invalidated semantic cache for %d updated documents.", len(updated_doc_ids))
        except Exception as cache_err:
            logger.warning("Semantic cache invalidation during ingest encountered soft error: %s", cache_err)

    finally:
        # 7. Drop Temporary Staging Table
        try:
            bq_client.delete_table(staging_table_id, not_found_ok=True)
            logger.info("Cleaned up temporary staging table %s", staging_table_name)
        except Exception as e:
            logger.warning("Failed to drop staging table %s: %s", staging_table_name, e)

    # 8. Automatically Ensure Vector Index DDL & Monitor Coverage
    ensure_vector_index(bq_client, project_id, dataset_id, table_name)
    check_vector_index_coverage(bq_client, project_id, dataset_id, table_name)

    return len(articles)


def reconcile_deleted_documents(
    bq_client: Any,
    project_id: str,
    dataset_id: str,
    table_name: str = "knowledge_articles",
    active_source_uris: Optional[list[str]] = None
) -> int:
    """
    Reconciliation Step: Soft-deletes (tombstones) documents that exist in BigQuery but are no longer present
    in the source directory/bucket.
    Ensures compliance by preventing removed SOPs/manuals from being cited by the agent.
    """
    if active_source_uris is None:
        logger.info("No active source URIs provided for reconciliation; skipping.")
        return 0

    full_target_table = f"`{project_id}.{dataset_id}.{table_name}`"
    from google.cloud import bigquery

    if not active_source_uris:
        # If active_source_uris is empty list, tombstone all currently active documents
        tombstone_sql = f"""
        UPDATE {full_target_table}
        SET is_deleted = TRUE, deleted_at = CURRENT_TIMESTAMP()
        WHERE (is_deleted IS NOT TRUE OR is_deleted = FALSE)
        """
        job_config = bigquery.QueryJobConfig()
    else:
        tombstone_sql = f"""
        UPDATE {full_target_table}
        SET is_deleted = TRUE, deleted_at = CURRENT_TIMESTAMP()
        WHERE (is_deleted IS NOT TRUE OR is_deleted = FALSE)
          AND source_uri NOT IN UNNEST(@active_source_uris)
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ArrayQueryParameter("active_source_uris", "STRING", active_source_uris)
            ]
        )

    try:
        logger.info("Reconciling deleted documents against %d active source URIs...", len(active_source_uris))
        job = bq_client.query(tombstone_sql, job_config=job_config)
        job.result()
        affected = getattr(job, "num_dml_affected_rows", 0) or 0
        if affected > 0:
            logger.warning("Tombstoned %d chunks corresponding to deleted source documents.", affected)
            try:
                from agent_core.app_utils.semantic_cache import get_semantic_cache
                cache = get_semantic_cache()
                find_tombstoned_sql = f"""
                SELECT DISTINCT id, parent_doc_id, system
                FROM {full_target_table}
                WHERE is_deleted = TRUE AND deleted_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 5 MINUTE)
                """
                tombstoned_query_job = bq_client.query(find_tombstoned_sql)
                tombstoned_rows = tombstoned_query_job.result()
                tombstoned_ids = set()
                for row in tombstoned_rows:
                    doc_id = getattr(row, "parent_doc_id", None) or getattr(row, "id", None)
                    sys_name = getattr(row, "system", None)
                    if doc_id:
                        tombstoned_ids.add((doc_id, sys_name))
                for doc_id, sys_name in tombstoned_ids:
                    cache.invalidate(article_id=doc_id, system=sys_name)
                if tombstoned_ids:
                    logger.info("Invalidated semantic cache for %d tombstoned documents.", len(tombstoned_ids))
            except Exception as cache_err:
                logger.warning("Semantic cache invalidation during reconciliation encountered soft error: %s", cache_err)
        else:
            logger.info("Reconciliation complete: 0 documents required tombstoning.")
        return int(affected)
    except Exception as e:
        logger.error("Failed to reconcile deleted documents: %s", e)
        raise


def purge_tombstoned_chunks(
    bq_client: Any,
    project_id: str,
    dataset_id: str,
    table_name: str = "knowledge_articles",
    older_than_days: int = 30
) -> int:
    """
    Hard-deletes tombstoned chunks that have been soft-deleted for longer than older_than_days.
    Executed on a scheduled maintenance cycle.
    """
    full_target_table = f"`{project_id}.{dataset_id}.{table_name}`"
    from google.cloud import bigquery

    purge_sql = f"""
    DELETE FROM {full_target_table}
    WHERE is_deleted = TRUE
      AND deleted_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @older_than_days DAY)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("older_than_days", "INT64", older_than_days)
        ]
    )
    try:
        logger.info("Purging tombstoned chunks older than %d days...", older_than_days)
        job = bq_client.query(purge_sql, job_config=job_config)
        job.result()
        affected = getattr(job, "num_dml_affected_rows", 0) or 0
        logger.info("Hard purged %d expired tombstone chunks.", affected)
        return int(affected)
    except Exception as e:
        logger.error("Failed to purge tombstoned chunks: %s", e)
        raise


def get_stale_chunks_for_reprocessing(
    bq_client: Any,
    project_id: str,
    dataset_id: str,
    table_name: str = "knowledge_articles",
    current_chunker_version: str = "1.0.0",
    current_parser_version: str = "1.0.0"
) -> list[dict[str, Any]]:
    """
    Identifies chunks that were produced with older parser or chunker versions for replay ingestion.
    """
    full_target_table = f"`{project_id}.{dataset_id}.{table_name}`"
    from google.cloud import bigquery

    stale_sql = f"""
    SELECT id, source_uri, parser_version, chunker_version, embedding_model, embedding_dim
    FROM {full_target_table}
    WHERE (parser_version != @parser_version OR parser_version IS NULL)
       OR (chunker_version != @chunker_version OR chunker_version IS NULL)
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("parser_version", "STRING", current_parser_version),
            bigquery.ScalarQueryParameter("chunker_version", "STRING", current_chunker_version),
        ]
    )
    try:
        rows = list(bq_client.query(stale_sql, job_config=job_config).result())
        stale_list = [dict(r) for r in rows]
        logger.info("Found %d stale chunks requiring reprocessing.", len(stale_list))
        return stale_list
    except Exception as e:
        logger.warning("Could not query stale chunks: %s", e)
        return []


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
        from agent_core.tools.enterprise_rag_mcp.knowledge_store import BigQueryVectorKnowledgeStore
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
