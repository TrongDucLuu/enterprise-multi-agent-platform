"""
Unit tests for Enterprise Knowledge Base Data Ingestion Pipeline.
"""

import os
import sys
import json
import hashlib
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


def test_parse_pdf_document_with_mock(monkeypatch, tmp_path):
    pdf_path = tmp_path / "manual.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 dummy pdf binary")

    mock_pypdf = MagicMock()
    mock_reader = MagicMock()
    mock_page_1 = MagicMock()
    mock_page_1.extract_text.return_value = "Tiêu Đề Hướng Dẫn PDF\nNội dung trang 1."
    mock_page_2 = MagicMock()
    mock_page_2.extract_text.return_value = "Nội dung trang 2 chi tiết."
    mock_reader.pages = [mock_page_1, mock_page_2]
    mock_pypdf.PdfReader.return_value = mock_reader

    monkeypatch.setitem(sys.modules, "pypdf", mock_pypdf)

    parsed = DocumentParser.parse_pdf(pdf_path)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "Tiêu Đề Hướng Dẫn PDF"
    assert "Nội dung trang 1." in parsed[0]["content"]
    assert "Nội dung trang 2" in parsed[0]["content"]
    assert parsed[0]["file_type"] == ".pdf"


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


def test_process_document_generates_deterministic_ids_and_content_hash():
    doc = {
        "title": "Hướng dẫn SAP MM",
        "system": "ERP",
        "category": "Procurement",
        "content": "Nội dung tài liệu kiểm thử quy trình mua sắm hàng hóa...",
        "source_uri": "gs://bucket/sap.md"
    }

    articles_1 = process_document(doc)
    articles_2 = process_document(doc)

    assert len(articles_1) == 1
    assert articles_1[0]["id"].startswith("ERP-KB-")
    assert articles_1[0]["id"] == articles_2[0]["id"]
    assert articles_1[0]["system"] == "ERP"
    assert "content_hash" in articles_1[0]
    expected_hash = hashlib.sha256(doc["content"].encode("utf-8")).hexdigest()
    assert articles_1[0]["content_hash"] == expected_hash


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


def test_ingest_articles_staging_table_and_merge(monkeypatch):
    """
    Verifies that ingest_articles_to_bigquery:
    1. Loads articles into a temporary staging table (batch load).
    2. Executes Atomic SQL MERGE from staging to target table.
    3. Cleans up orphaned chunks on target table.
    4. Deletes temporary staging table.
    """
    mock_bq_module = MagicMock()
    mock_client = MagicMock()
    
    mock_load_job = MagicMock()
    mock_client.load_table_from_json.return_value = mock_load_job
    
    mock_query_job = MagicMock()
    mock_query_job.num_dml_affected_rows = 1
    mock_client.query.return_value = mock_query_job
    
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
        "content_hash": hashlib.sha256(b"Chi tiet loi").hexdigest(),
        "updated_at": "2026-08-31T00:00:00Z"
    }]

    count = ingest_articles_to_bigquery(
        sample_articles,
        project_id="test-proj",
        dataset_id="test_kb",
        table_name="knowledge_articles"
    )

    assert count == 1
    # 1. Staging table was created
    assert mock_client.create_table.called
    # 2. Batch load into staging table
    assert mock_client.load_table_from_json.called
    # 3. MERGE query executed
    all_queries = [c[0][0] for c in mock_client.query.call_args_list]
    assert any("MERGE `test-proj.test_kb.knowledge_articles` T" in q for q in all_queries)
    # 4. Staging table deleted
    assert mock_client.delete_table.called


def test_cdc_reuses_existing_embeddings_when_content_hash_matches(monkeypatch):
    """
    Verifies CDC (Change Data Capture) pre-check:
    When an article's content_hash matches existing row in BigQuery, its embedding is reused
    without calling Vertex AI Embedding API again.
    """
    mock_bq_module = MagicMock()
    mock_client = MagicMock()

    content_str = "Quy trình xin nghỉ phép qua phần mềm HRM."
    curr_hash = hashlib.sha256(content_str.encode("utf-8")).hexdigest()
    pre_existing_vector = [0.123] * 768

    # Mock CDC query returning existing hash & vector
    mock_cdc_row = MagicMock()
    mock_cdc_row.id = "HRM-KB-111"
    mock_cdc_row.content_hash = curr_hash
    mock_cdc_row.embedding = pre_existing_vector

    mock_cdc_job = MagicMock()
    mock_cdc_job.result.return_value = [mock_cdc_row]
    
    # Return CDC result on first query, and general jobs for subsequent queries
    mock_client.query.return_value = mock_cdc_job
    mock_bq_module.Client.return_value = mock_client
    monkeypatch.setattr("google.cloud.bigquery", mock_bq_module, raising=False)

    articles = [{
        "id": "HRM-KB-111",
        "system": "HRM",
        "title": "Nghỉ phép HRM",
        "category": "TimeOff",
        "content": content_str,
        "keywords": ["nghỉ", "phép"],
        "source_uri": "docs/hrm_leave.md",
        "content_hash": curr_hash,
        "updated_at": "2026-08-31T00:00:00Z"
    }]

    count = ingest_articles_to_bigquery(
        articles,
        project_id="test-proj",
        dataset_id="test_kb",
        table_name="knowledge_articles"
    )

    assert count == 1
    # Verify embedding was reused from pre-existing vector without re-calling Vertex AI
    assert articles[0]["embedding"] == pre_existing_vector


def test_document_shrinks_triggers_orphaned_chunk_cleanup(monkeypatch):
    """
    Simulates document update shrinking from 7 chunks to 5 chunks:
    Verifies that ingest_articles_to_bigquery triggers DELETE DML referencing staging table IDs.
    """
    mock_bq_module = MagicMock()
    mock_client = MagicMock()
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
            "content_hash": hashlib.sha256(f"Nội dung mới phần {i}".encode()).hexdigest(),
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
    assert any("WHERE source_uri IN UNNEST(@source_uris)" in q for q in all_queries)
    assert any("NOT IN (\n                SELECT id FROM `test-proj.test_kb.knowledge_articles_staging_" in q or "SELECT id FROM" in q for q in all_queries)
