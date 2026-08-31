"""
Unit tests for Enterprise Knowledge Base Data Ingestion Pipeline.
"""

import os
import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from scripts.ingest_knowledge_base import (
    DocumentParser,
    chunk_text,
    process_document,
    ingest_articles_to_bigquery,
)


def test_parse_markdown_document(tmp_path):
    doc_path = tmp_path / "test_doc.md"
    doc_path.write_text("# Hướng Dẫn Kỹ Thuật ERP\n\nNội dung chi tiết xử lý lỗi.", encoding="utf-8")

    parsed = DocumentParser.parse_markdown_or_text(doc_path)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "Hướng Dẫn Kỹ Thuật ERP"
    assert "Nội dung chi tiết" in parsed[0]["content"]
    assert parsed[0]["file_type"] == ".md"


def test_parse_jsonl_document(tmp_path):
    jsonl_path = tmp_path / "test_kb.jsonl"
    jsonl_path.write_text(
        json.dumps({
            "id": "HRM-KB-999",
            "system": "HRM",
            "title": "Chính sách bảo hiểm",
            "category": "Benefits",
            "content": "Quy định bảo hiểm y tế doanh nghiệp.",
            "keywords": ["bảo hiểm", "y tế"]
        }) + "\n",
        encoding="utf-8"
    )

    parsed = DocumentParser.parse_jsonl(jsonl_path)
    assert len(parsed) == 1
    assert parsed[0]["id"] == "HRM-KB-999"
    assert parsed[0]["system"] == "HRM"
    assert parsed[0]["category"] == "Benefits"


def test_chunk_text_splits_long_content():
    short_text = "Câu văn ngắn dưới 100 ký tự."
    assert len(chunk_text(short_text, max_chunk_size=500)) == 1

    long_text = "Đoạn văn 1.\n\n" + ("Nội dung bài viết rất dài cần phân tách thành nhiều chunks. " * 30)
    chunks = chunk_text(long_text, max_chunk_size=300, overlap=50)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) > 0


def test_process_document_generates_deterministic_ids():
    doc = {
        "title": "Hướng dẫn SAP MM",
        "system": "ERP",
        "category": "Procurement",
        "content": "Nội dung tài liệu...",
        "source_uri": "gs://bucket/sap.md"
    }

    articles_1 = process_document(doc)
    articles_2 = process_document(doc)

    assert len(articles_1) == 1
    assert articles_1[0]["id"].startswith("ERP-KB-")
    assert articles_1[0]["id"] == articles_2[0]["id"]
    assert articles_1[0]["system"] == "ERP"


def test_process_document_rejects_unconfigured_system():
    doc = {
        "title": "Tài liệu bí mật",
        "system": "NON_EXISTENT_SYSTEM",
        "content": "Nội dung...",
    }
    with pytest.raises(ValueError, match="không hợp lệ"):
        process_document(doc)


def test_process_document_rejects_reserved_keyword_all():
    doc = {
        "title": "Tài liệu chung",
        "system": "ALL",
        "content": "Nội dung...",
    }
    with pytest.raises(ValueError, match="từ khóa dành riêng"):
        process_document(doc)


def test_ingest_articles_to_bigquery_with_mock(monkeypatch):
    mock_bq_module = MagicMock()
    mock_client = MagicMock()
    mock_client.insert_rows_json.return_value = []  # No errors
    mock_bq_module.Client.return_value = mock_client
    monkeypatch.setattr("google.cloud.bigquery", mock_bq_module, raising=False)

    sample_articles = [{
        "id": "ERP-KB-001",
        "system": "ERP",
        "title": "Lỗi PO SAP",
        "category": "Procurement",
        "content": "Chi tiết lỗi và giải pháp...",
        "keywords": ["sap", "po"],
        "source_uri": "data/sample.md",
        "updated_at": "2026-08-31T00:00:00Z"
    }]

    count = ingest_articles_to_bigquery(
        sample_articles,
        project_id="test-proj",
        dataset_id="test_kb",
        table_name="knowledge_articles"
    )

    assert count == 1
    assert mock_client.insert_rows_json.called
    inserted_rows = mock_client.insert_rows_json.call_args[0][1]
    assert len(inserted_rows) == 1
    assert "embedding" in inserted_rows[0]
    assert len(inserted_rows[0]["embedding"]) == 768


def test_cleanup_orphaned_chunks_executes_dml_delete(monkeypatch):
    """Verify that cleanup_orphaned_chunks runs parameterized BigQuery DELETE DML."""
    from scripts.ingest_knowledge_base import cleanup_orphaned_chunks

    mock_bq_module = MagicMock()
    mock_client = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.num_dml_affected_rows = 2
    mock_client.query.return_value = mock_query_job
    mock_bq_module.Client.return_value = mock_client
    monkeypatch.setattr("google.cloud.bigquery", mock_bq_module, raising=False)

    source_uri = "docs/sap_guide.md"
    current_ids = ["ERP-KB-CHUNK1", "ERP-KB-CHUNK2", "ERP-KB-CHUNK3"]

    deleted = cleanup_orphaned_chunks(
        mock_client,
        project_id="test-proj",
        dataset_id="test_kb",
        table_name="knowledge_articles",
        source_uri=source_uri,
        current_chunk_ids=current_ids
    )

    assert deleted == 2
    assert mock_client.query.called
    query_str = mock_client.query.call_args[0][0]
    assert "DELETE FROM `test-proj.test_kb.knowledge_articles`" in query_str
    assert "WHERE source_uri = @source_uri" in query_str
    assert "AND id NOT IN UNNEST(@current_chunk_ids)" in query_str


def test_document_shrinks_triggers_orphaned_chunk_cleanup(monkeypatch):
    """
    Simulates document update shrinking from 7 chunks to 5 chunks:
    Verifies that ingest_articles_to_bigquery triggers cleanup for the remaining 5 chunk IDs.
    """
    mock_bq_module = MagicMock()
    mock_client = MagicMock()
    mock_client.insert_rows_json.return_value = []
    mock_query_job = MagicMock()
    mock_query_job.num_dml_affected_rows = 2  # 2 old orphaned chunks deleted
    mock_client.query.return_value = mock_query_job
    mock_bq_module.Client.return_value = mock_client
    monkeypatch.setattr("google.cloud.bigquery", mock_bq_module, raising=False)

    # Document now only has 5 chunks
    five_chunks = [
        {
            "id": f"ERP-KB-CHUNK{i}",
            "system": "ERP",
            "title": f"Tài liệu cập nhật (Phần {i}/5)",
            "category": "Procurement",
            "content": f"Nội dung mới phần {i}...",
            "keywords": ["sap"],
            "source_uri": "docs/procurement_v2.md",
            "updated_at": "2026-08-31T00:00:00Z"
        }
        for i in range(1, 6)
    ]

    count = ingest_articles_to_bigquery(
        five_chunks,
        project_id="test-proj",
        dataset_id="test_kb",
        table_name="knowledge_articles"
    )

    assert count == 5
    assert mock_client.query.called
    all_queries = [c[0][0] for c in mock_client.query.call_args_list]
    assert any("DELETE FROM `test-proj.test_kb.knowledge_articles`" in q for q in all_queries)
    assert any("WHERE source_uri = @source_uri" in q for q in all_queries)
    assert any("AND id NOT IN UNNEST(@current_chunk_ids)" in q for q in all_queries)

