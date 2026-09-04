"""
Unit tests for Ingestion Guards and KB Metadata Verification.
Verifies:
1. Embedding dimension enforcement (768 dimensions only, fails on mismatch).
2. ensure_vector_index fails loud on non-recoverable errors, succeeds on already exists.
3. wait_for_vector_index_ready polling behavior.
4. kb_metadata persistence and BigQueryVectorKnowledgeStore startup refusal on mismatch.
"""

from unittest.mock import MagicMock, patch
import pytest

from scripts.ingest.loaders import (
    ensure_vector_index,
    wait_for_vector_index_ready,
    check_vector_index_coverage,
    get_kb_metadata_schema,
    record_kb_metadata,
    ingest_articles_to_bigquery,
)
from agent_core.tools.enterprise_rag_mcp.knowledge_store import BigQueryVectorKnowledgeStore


def test_embedding_dimension_guard_enforces_768():
    mock_bq = MagicMock()
    articles_bad_dim = [
        {
            "id": "doc1#0",
            "system": "IT",
            "title": "Title",
            "content": "Content",
            "embedding": [0.1] * 512,
            "updated_at": "2026-09-04T00:00:00Z",
        }
    ]

    with pytest.raises(ValueError, match="Embedding dimension mismatch"):
        ingest_articles_to_bigquery(
            articles=articles_bad_dim,
            project_id="test-proj",
            dataset_id="test-ds",
            bq_client=mock_bq,
        )


def test_ensure_vector_index_fails_loud_on_error():
    mock_bq = MagicMock()
    mock_bq.query.side_effect = Exception("Syntax error in DDL statement")

    with pytest.raises(RuntimeError, match="Failed to create BigQuery Vector Index"):
        ensure_vector_index(
            bq_client=mock_bq,
            project_id="test-proj",
            dataset_id="test-ds",
        )


def test_ensure_vector_index_tolerates_already_exists():
    mock_bq = MagicMock()
    mock_bq.query.side_effect = Exception("Vector index knowledge_articles_vector_idx Already Exists")

    ensure_vector_index(
        bq_client=mock_bq,
        project_id="test-proj",
        dataset_id="test-ds",
    )


def test_wait_for_vector_index_ready_reaches_target():
    mock_bq = MagicMock()
    mock_row = MagicMock()
    mock_row.index_status = "ACTIVE"
    mock_row.coverage_percentage = 98.5
    mock_row.unindexed_row_count = 10
    mock_row.total_row_count = 5000

    mock_job = MagicMock()
    mock_job.result.return_value = [mock_row]
    mock_bq.query.return_value = mock_job

    status = wait_for_vector_index_ready(
        bq_client=mock_bq,
        project_id="test-proj",
        dataset_id="test-ds",
        target_coverage=95.0,
        timeout_seconds=2.0,
        poll_interval=0.1,
    )
    assert status["coverage_percentage"] == 98.5
    assert status["index_status"] == "ACTIVE"


def test_kb_metadata_schema():
    schema = get_kb_metadata_schema()
    field_names = [f.name for f in schema]
    assert "metadata_key" in field_names
    assert "embedding_model" in field_names
    assert "embedding_dim" in field_names
    assert "kb_version" in field_names


def test_knowledge_store_startup_refusal_on_metadata_mismatch():
    mock_bq = MagicMock()
    mock_row = MagicMock()
    mock_row.metadata_key = "active_kb"
    mock_row.embedding_model = "textembedding-gecko@003"
    mock_row.embedding_dim = 768
    mock_row.kb_version = "1.0.0"

    mock_job = MagicMock()
    mock_job.result.return_value = [mock_row]
    mock_bq.query.return_value = mock_job

    store = BigQueryVectorKnowledgeStore(
        project_id="test-proj",
        dataset_id="test-ds",
        table_name="knowledge_articles",
        embedding_model="text-multilingual-embedding-002",
        bq_client=mock_bq,
    )

    with pytest.raises(RuntimeError, match="Knowledge Base metadata mismatch"):
        store._verify_kb_metadata()
