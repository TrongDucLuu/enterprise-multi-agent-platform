"""
BigQuery Loaders and Vector Index Management for Enterprise Knowledge Base.
Provides idempotent upsert (atomic MERGE), staging table orchestration, orphaned chunk cleanup,
vector index DDL creation, and coverage monitoring.
"""

import uuid
import logging
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
    STORING (system, category, id, title, content, section_hierarchy)
    OPTIONS(distance_type='COSINE', index_type='IVF')
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
        return {"index_status": "ERROR", "error": str(e)}


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
        bigquery.SchemaField(
            "section_hierarchy",
            "RECORD",
            mode="NULLABLE",
            fields=[
                bigquery.SchemaField("h1", "STRING", mode="NULLABLE"),
                bigquery.SchemaField("h2", "STRING", mode="NULLABLE"),
                bigquery.SchemaField("h3", "STRING", mode="NULLABLE"),
            ]
        ),
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
            T.system = S.system,
            T.title = S.title,
            T.category = S.category,
            T.content = S.content,
            T.keywords = S.keywords,
            T.embedding = S.embedding,
            T.source_uri = S.source_uri,
            T.content_hash = S.content_hash,
            T.section_hierarchy = S.section_hierarchy,
            T.updated_at = S.updated_at
        WHEN NOT MATCHED THEN
          INSERT (id, system, title, category, content, keywords, embedding, source_uri, content_hash, section_hierarchy, updated_at)
          VALUES (S.id, S.system, S.title, S.category, S.content, S.keywords, S.embedding, S.source_uri, S.content_hash, S.section_hierarchy, S.updated_at);
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
