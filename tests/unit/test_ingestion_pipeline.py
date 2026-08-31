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


def test_parse_jsonl_disambiguates_same_title_without_source_uri_using_line_no(tmp_path):
    """
    Verifies that two JSONL records with identical titles and no explicit source_uri
    receive distinct source_uris (#L1, #L2) and produce distinct deterministic IDs.
    """
    jsonl_file = tmp_path / "colliding.jsonl"
    jsonl_file.write_text(
        json.dumps({"system": "ERP", "title": "Cấu hình SAP", "content": "Nội dung 1"}) + "\n" +
        json.dumps({"system": "ERP", "title": "Cấu hình SAP", "content": "Nội dung 2"}) + "\n",
        encoding="utf-8"
    )

    parsed = DocumentParser.parse_jsonl(jsonl_file)
    assert len(parsed) == 2
    assert parsed[0]["source_uri"].endswith("#L1")
    assert parsed[1]["source_uri"].endswith("#L2")

    articles_1 = process_document(parsed[0])
    articles_2 = process_document(parsed[1])

    assert len(articles_1) == 1
    assert len(articles_2) == 1
    # Distinct IDs guaranteed!
    assert articles_1[0]["id"] != articles_2[0]["id"]


def test_ingest_articles_deduplicates_duplicate_ids_and_uses_sql_qualify(monkeypatch, caplog):
    """
    Verifies that ingest_articles_to_bigquery:
    1. Deduplicates input chunks having identical ID in Python, keeping the latest.
    2. Logs a clear warning for operators.
    3. Uses QUALIFY ROW_NUMBER() in MERGE SQL as a fail-safe against BigQuery MERGE runtime error.
    """
    mock_bq_module = MagicMock()
    mock_client = MagicMock()
    mock_load_job = MagicMock()
    mock_client.load_table_from_json.return_value = mock_load_job
    mock_query_job = MagicMock()
    mock_query_job.num_dml_affected_rows = 0
    mock_client.query.return_value = mock_query_job
    mock_bq_module.Client.return_value = mock_client
    monkeypatch.setattr("google.cloud.bigquery", mock_bq_module, raising=False)

    colliding_articles = [
        {
            "id": "ERP-KB-02738B5E",
            "system": "ERP",
            "title": "Bản ghi cũ",
            "category": "Finance",
            "content": "Nội dung cũ",
            "keywords": ["sap"],
            "source_uri": "data/erp.jsonl",
            "content_hash": "hash_old",
            "updated_at": "2026-08-31T01:00:00Z"
        },
        {
            "id": "ERP-KB-02738B5E",
            "system": "ERP",
            "title": "Bản ghi mới",
            "category": "Finance",
            "content": "Nội dung mới",
            "keywords": ["sap"],
            "source_uri": "data/erp.jsonl",
            "content_hash": "hash_new",
            "updated_at": "2026-08-31T02:00:00Z"
        }
    ]

    import logging
    with caplog.at_level(logging.WARNING):
        count = ingest_articles_to_bigquery(
            colliding_articles,
            project_id="test-proj",
            dataset_id="test_kb",
            table_name="knowledge_articles"
        )

    assert count == 1  # Deduplicated from 2 to 1
    assert "Phát hiện 1 chunk trùng ID" in caplog.text
    assert "ERP-KB-02738B5E" in caplog.text

    # Verify load job received the deduped list with the latest record
    loaded_articles = mock_client.load_table_from_json.call_args[0][0]
    assert len(loaded_articles) == 1
    assert loaded_articles[0]["title"] == "Bản ghi mới"

    # Verify QUALIFY ROW_NUMBER() is present in MERGE query
    all_queries = [c[0][0] for c in mock_client.query.call_args_list]
    merge_queries = [q for q in all_queries if "MERGE `test-proj.test_kb.knowledge_articles` T" in q]
    assert len(merge_queries) == 1
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY updated_at DESC) = 1" in merge_queries[0]


def test_parse_markdown_extracts_sections(tmp_path):
    doc_path = tmp_path / "sap_guide.md"
    content = (
        "# Hướng Dẫn SAP Mua Hàng\n\n"
        "Tổng quan về quy trình mua hàng doanh nghiệp.\n\n"
        "## Bước 1: Tạo Purchase Requisition (PR)\n\n"
        "Sử dụng giao dịch ME51N để tạo yêu cầu mua sắm. Cần điền mã vật tư và số lượng.\n\n"
        "## Bước 2: Phê duyệt Purchase Order (PO)\n\n"
        "Sử dụng giao dịch ME29N để phê duyệt đơn đặt hàng bởi cấp quản lý."
    )
    doc_path.write_text(content, encoding="utf-8")

    parsed = DocumentParser.parse_markdown_or_text(doc_path)
    assert len(parsed) == 1
    doc = parsed[0]
    assert doc["title"] == "Hướng Dẫn SAP Mua Hàng"
    assert len(doc["sections"]) == 3
    assert doc["sections"][0]["heading"] == "Hướng Dẫn SAP Mua Hàng"
    assert doc["sections"][1]["heading"] == "Bước 1: Tạo Purchase Requisition (PR)"
    assert "ME51N" in doc["sections"][1]["content"]
    assert doc["sections"][2]["heading"] == "Bước 2: Phê duyệt Purchase Order (PO)"
    assert "ME29N" in doc["sections"][2]["content"]


def test_parse_docx_extracts_sections(monkeypatch, tmp_path):
    docx_path = tmp_path / "policy.docx"
    docx_path.write_bytes(b"dummy docx binary")

    mock_docx = MagicMock()
    mock_doc = MagicMock()

    p1 = MagicMock()
    p1.text = "Chính Sách Nhân Sự"
    p1.style.name = "Heading 1"

    p2 = MagicMock()
    p2.text = "Quy định làm việc từ xa và chấm công linh hoạt trong doanh nghiệp."
    p2.style.name = "Normal"

    p3 = MagicMock()
    p3.text = "Chế Độ Phúc Lợi"
    p3.style.name = "Heading 2"

    p4 = MagicMock()
    p4.text = "Chi tiết về bảo hiểm sức khỏe và trợ cấp đi lại hàng tháng."
    p4.style.name = "Normal"

    mock_doc.paragraphs = [p1, p2, p3, p4]
    mock_docx.Document.return_value = mock_doc
    monkeypatch.setitem(sys.modules, "docx", mock_docx)

    parsed = DocumentParser.parse_docx(docx_path)
    assert len(parsed) == 1
    doc = parsed[0]
    assert doc["title"] == "Chính Sách Nhân Sự"
    assert len(doc["sections"]) == 2
    assert doc["sections"][0]["heading"] == "Chính Sách Nhân Sự"
    assert "làm việc từ xa" in doc["sections"][0]["content"]
    assert doc["sections"][1]["heading"] == "Chế Độ Phúc Lợi"
    assert "bảo hiểm sức khỏe" in doc["sections"][1]["content"]


def test_is_well_structured():
    from scripts.ingest_knowledge_base import is_well_structured

    # Case 1: Less than 2 sections -> False
    assert not is_well_structured([])
    assert not is_well_structured([{"heading": "Intro", "content": "A" * 300}])

    # Case 2: One section dominates > 65% of document -> False
    unbalanced_sections = [
        {"heading": "Short Intro", "content": "A" * 50},
        {"heading": "Giant Section", "content": "B" * 1000},  # 1000 / 1050 = 95% > 65%
    ]
    assert not is_well_structured(unbalanced_sections, max_section_ratio=0.65)

    # Case 3: Avg section length < 100 -> False
    tiny_sections = [
        {"heading": "Sec 1", "content": "Short text."},
        {"heading": "Sec 2", "content": "Tiny part."},
        {"heading": "Sec 3", "content": "Another short."},
    ]
    assert not is_well_structured(tiny_sections, min_avg_length=100)

    # Case 4: Balanced well-structured document -> True
    balanced_sections = [
        {"heading": "Sec 1", "content": "Nội dung phần 1 đầy đủ thông tin chi tiết kỹ thuật. " * 5},
        {"heading": "Sec 2", "content": "Nội dung phần 2 hướng dẫn từng bước cấu hình. " * 5},
        {"heading": "Sec 3", "content": "Nội dung phần 3 danh mục lỗi thường gặp khi thao tác. " * 5},
    ]
    assert is_well_structured(balanced_sections, max_section_ratio=0.65, min_avg_length=100)


def test_chunk_by_sections_heading_attachment_and_large_section_split():
    from scripts.ingest_knowledge_base import chunk_by_sections

    sections = [
        {
            "heading": "Cấu hình Kết Nối",
            "content": "Hướng dẫn cấu hình kết nối database ERP qua cổng 3306 an toàn."
        },
        {
            "heading": "Khắc phục Sự Cố Lớn",
            "content": ("Chi tiết các bước chẩn đoán deadlock và phân quyền người dùng. " * 20)
        }
    ]

    chunks = chunk_by_sections(sections, max_chunk_size=400, overlap=50)
    assert len(chunks) >= 2
    # Section 1 fits in max_chunk_size with header attached
    assert chunks[0].startswith("## Cấu hình Kết Nối")
    assert "3306" in chunks[0]

    # Section 2 is split recursively, each sub-chunk retains the heading
    for c in chunks[1:]:
        assert c.startswith("## Khắc phục Sự Cố Lớn")


def test_recursive_chunk_text():
    # Test paragraph separator priority
    multi_p = "Đoạn 1.\n\nĐoạn 2.\n\nĐoạn 3."
    chunks = chunk_text(multi_p, max_chunk_size=12, overlap=0)
    assert len(chunks) == 3
    assert chunks[0] == "Đoạn 1."
    assert chunks[1] == "Đoạn 2."
    assert chunks[2] == "Đoạn 3."

    # Test sentence separator fallback
    long_single_p = "Câu số 1 rất quan trọng. Câu số 2 cũng rất quan trọng. Câu số 3 cần lưu ý."
    chunks_s = chunk_text(long_single_p, max_chunk_size=35, overlap=5)
    assert len(chunks_s) >= 2

    # Test hard char split fallback when no delimiters exist
    no_delim = "A" * 100
    chunks_hard = chunk_text(no_delim, max_chunk_size=30, overlap=10)
    assert len(chunks_hard) == 5
    for ch in chunks_hard:
        assert len(ch) <= 30



def test_document_ai_parser_mapping_and_fail_closed(monkeypatch, tmp_path):
    pdf_path = tmp_path / "test_doc_ai.pdf"
    pdf_path.write_bytes(b"%PDF-1.4 sample content")

    # Mock Document AI client response
    mock_docai_module = MagicMock()
    mock_client = MagicMock()
    
    mock_document = {
        "text": "TIÊU ĐỀ TÀI LIỆU\n\nNội dung mở đầu.\n\nQUY TRÌNH XỬ LÝ\n\nChi tiết quy trình bước 1.",
        "pages": [
            {
                "blocks": [
                    {
                        "type_": "heading-1",
                        "text": "TIÊU ĐỀ TÀI LIỆU",
                    },
                    {
                        "type_": "paragraph",
                        "text": "Nội dung mở đầu.",
                    },
                    {
                        "type_": "heading-2",
                        "text": "QUY TRÌNH XỬ LÝ",
                    },
                    {
                        "type_": "paragraph",
                        "text": "Chi tiết quy trình bước 1.",
                    }
                ]
            }
        ]
    }

    mock_process_result = MagicMock()
    mock_process_result.document = mock_document
    mock_client.process_document.return_value = mock_process_result
    mock_docai_module.DocumentProcessorServiceClient.return_value = mock_client
    mock_docai_module.RawDocument = MagicMock()
    mock_docai_module.ProcessRequest = MagicMock()

    monkeypatch.setitem(sys.modules, "google.cloud.documentai", mock_docai_module)

    config = {
        "pdf_parser": "document_ai",
        "document_ai_processor_id": "projects/123/locations/us/processors/abc",
        "document_ai_timeout_seconds": 10.0,
        "document_ai_max_retries": 1,
    }

    parsed = DocumentParser.parse_pdf_document_ai(pdf_path, config)
    assert len(parsed) == 1
    assert parsed[0]["title"] == "TIÊU ĐỀ TÀI LIỆU"
    assert len(parsed[0]["sections"]) == 2
    assert parsed[0]["sections"][0]["heading"] == "TIÊU ĐỀ TÀI LIỆU"
    assert "Nội dung mở đầu." in parsed[0]["sections"][0]["content"]
    assert parsed[0]["sections"][1]["heading"] == "QUY TRÌNH XỬ LÝ"
    assert "Chi tiết quy trình" in parsed[0]["sections"][1]["content"]

    # Test Fail-Closed on recurring API errors
    mock_client.process_document.side_effect = RuntimeError("Service Unavailable")
    with pytest.raises(RuntimeError, match="Document AI parsing failed"):
        DocumentParser.parse_pdf_document_ai(pdf_path, config)


def test_markdown_parser_extracts_section_hierarchy(tmp_path):
    doc_path = tmp_path / "hierarchy_guide.md"
    doc_path.write_text(
        "# Sổ Tay Vận Hành ERP\n\n"
        "Tổng quan toàn bộ hệ thống hoạch định tài nguyên doanh nghiệp SAP S/4HANA cho khối tài chính và mua sắm.\n\n"
        "## Quản Lý Đơn Mua Hàng (PO)\n\n"
        "Quy trình tạo đơn PO trong hệ thống SAP thông qua giao dịch ME21N và quản lý trạng thái luồng phê duyệt nội bộ.\n\n"
        "### Phê Duyệt PO Cấp Quản Lý\n\n"
        "Chi tiết hạn mức phê duyệt đơn mua hàng PO từ cấp Giám đốc khối và các điều kiện kích hoạt workflow ngoại lệ.",
        encoding="utf-8"
    )

    parsed = DocumentParser.parse_markdown_or_text(doc_path)
    assert len(parsed) == 1
    sections = parsed[0]["sections"]
    assert len(sections) == 3

    assert sections[0]["hierarchy"] == {"h1": "Sổ Tay Vận Hành ERP", "h2": None, "h3": None}
    assert sections[1]["hierarchy"] == {"h1": "Sổ Tay Vận Hành ERP", "h2": "Quản Lý Đơn Mua Hàng (PO)", "h3": None}
    assert sections[2]["hierarchy"] == {"h1": "Sổ Tay Vận Hành ERP", "h2": "Quản Lý Đơn Mua Hàng (PO)", "h3": "Phê Duyệt PO Cấp Quản Lý"}

    articles = process_document(parsed[0])
    assert len(articles) == 3
    assert articles[0]["section_hierarchy"]["h1"] == "Sổ Tay Vận Hành ERP"
    assert articles[1]["section_hierarchy"]["h2"] == "Quản Lý Đơn Mua Hàng (PO)"
    assert articles[2]["section_hierarchy"]["h3"] == "Phê Duyệt PO Cấp Quản Lý"



def test_ensure_vector_index_contains_storing_clause():
    from scripts.ingest_knowledge_base import ensure_vector_index
    mock_bq = MagicMock()

    ensure_vector_index(mock_bq, project_id="my-proj", dataset_id="my_kb", table_name="articles")
    assert mock_bq.query.called
    ddl = mock_bq.query.call_args[0][0]
    assert "STORING (system, category, id, title, content, section_hierarchy, source_uri, owner, effective_date, expiry_date, is_deleted)" in ddl
    assert "OPTIONS(distance_type='COSINE', index_type='IVF')" in ddl


def test_check_vector_index_coverage_diagnostics(caplog):
    from scripts.ingest_knowledge_base import check_vector_index_coverage
    import logging

    mock_bq = MagicMock()
    
    # Case 1: Coverage = 0% and TEMPORARILY DISABLED (< 10 MB small KB)
    mock_row_disabled = MagicMock()
    mock_row_disabled.table_name = "knowledge_articles"
    mock_row_disabled.index_name = "knowledge_articles_vector_idx"
    mock_row_disabled.index_status = "TEMPORARILY DISABLED"
    mock_row_disabled.coverage_percentage = 0.0
    mock_row_disabled.unindexed_row_count = 0
    mock_row_disabled.total_row_count = 50

    mock_bq.query.return_value.result.return_value = [mock_row_disabled]

    with caplog.at_level(logging.INFO):
        res1 = check_vector_index_coverage(mock_bq, "p", "d")
        assert res1["index_status"] == "TEMPORARILY DISABLED"
        assert "TEMPORARILY DISABLED" in caplog.text
        assert "Exact Cosine Search" in caplog.text

    # Case 2: Coverage = 100% active index
    caplog.clear()
    mock_row_active = MagicMock()
    mock_row_active.table_name = "knowledge_articles"
    mock_row_active.index_name = "knowledge_articles_vector_idx"
    mock_row_active.index_status = "ACTIVE"
    mock_row_active.coverage_percentage = 100.0
    mock_row_active.unindexed_row_count = 0
    mock_row_active.total_row_count = 10000

    mock_bq.query.return_value.result.return_value = [mock_row_active]

    with caplog.at_level(logging.INFO):
        res2 = check_vector_index_coverage(mock_bq, "p", "d")
        assert res2["index_status"] == "ACTIVE"
        assert res2["coverage_percentage"] == 100.0
        assert "đang hoạt động tốt" in caplog.text


def test_pipeline_version_constants_and_chunk_metadata():
    """
    P1.4 Version Ingestion Pipeline:
    Verifies that PARSER_VERSION, CHUNKER_VERSION, EMBEDDING_MODEL, and EMBEDDING_DIM
    are correctly stamped onto all generated chunks.
    """
    from scripts.ingest.parsers import PARSER_VERSION
    from scripts.ingest.chunkers import CHUNKER_VERSION, process_document
    from scripts.ingest.embedders import EMBEDDING_MODEL, EMBEDDING_DIM

    assert PARSER_VERSION == "1.0.0"
    assert CHUNKER_VERSION == "1.0.0"
    assert EMBEDDING_MODEL == "text-embedding-005"
    assert EMBEDDING_DIM == 768

    doc_info = {
        "source_uri": "docs/test_policy.md",
        "system": "ERP",
        "title": "Test Policy",
        "content": "Phần 1: Quy định chung.\n\nPhần 2: Hướng dẫn chi tiết.",
        "owner": "governance@company.com",
        "effective_date": "2026-01-01",
        "expiry_date": "2027-01-01",
    }

    chunks = process_document(doc_info)
    assert len(chunks) > 0
    for c in chunks:
        assert c["parser_version"] == "1.0.0"
        assert c["chunker_version"] == "1.0.0"
        assert c["embedding_model"] == "text-embedding-005"
        assert c["embedding_dim"] == 768
        assert c["owner"] == "governance@company.com"
        assert c["effective_date"] == "2026-01-01"
        assert c["expiry_date"] == "2027-01-01"
        assert c["is_deleted"] is False
        assert c["deleted_at"] is None


def test_dead_letter_queue_captures_unparseable_files(tmp_path):
    """
    P2.8 Ingestion Observability & DLQ:
    Verifies that unreadable/corrupted files are appended to Dead Letter Queue (DLQ)
    and logged as WARNING/ERROR without terminating the pipeline.
    """
    import time
    from scripts.ingest.parsers import DocumentParser

    # Create unparseable file path
    non_existent = tmp_path / "non_existent.md"
    dlq = []

    try:
        DocumentParser.parse_markdown_or_text(non_existent)
    except Exception as exc:
        dlq.append({
            "source_uri": str(non_existent),
            "stage": "PARSING",
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "failed_at": time.time(),
        })

    assert len(dlq) == 1
    assert dlq[0]["stage"] == "PARSING"
    assert dlq[0]["error_type"] == "FileNotFoundError"


def test_get_stale_chunks_for_reprocessing():
    """
    P1.4 / P2.8 Stale Chunk Query:
    Verifies SQL generation for identifying chunks produced by older parser or chunker versions.
    """
    from scripts.ingest.loaders import get_stale_chunks_for_reprocessing

    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_row = MagicMock()
    mock_row.id = "ERP-OLD-001"
    mock_row.parser_version = "0.9.0"
    mock_row.chunker_version = "0.9.0"
    mock_row.source_uri = "docs/old.md"
    mock_query_job.result.return_value = [mock_row]
    mock_bq.query.return_value = mock_query_job

    stale = get_stale_chunks_for_reprocessing(
        mock_bq,
        project_id="corp-ai",
        dataset_id="kb_prod",
        table_name="articles",
        current_chunker_version="1.0.0",
        current_parser_version="1.0.0",
    )

    assert len(stale) == 1
    assert mock_bq.query.called
    sql = mock_bq.query.call_args[0][0]
    assert "parser_version != @parser_version" in sql
    assert "chunker_version != @chunker_version" in sql


def test_persist_and_read_dead_letter_queue(tmp_path, caplog):
    """
    P2.3: Verifies that unparseable documents in DLQ are:
    1. Persisted to durable storage independently of process lifetime.
    2. Read back correctly via read_persisted_dead_letter_queue.
    3. Trigger a structured CRITICAL alert log for Cloud Monitoring alerting.
    """
    import logging
    from scripts.ingest.loaders import persist_dead_letter_queue, read_persisted_dead_letter_queue

    caplog.set_level(logging.CRITICAL)

    dlq_file = tmp_path / "dlq_store.jsonl"
    fake_dlq = [
        {
            "file": "data/corrupted_manual.docx",
            "stage": "parsing",
            "error": "BadZipFile: File is not a zip file",
            "doc": {"title": "Corrupted SOP Document"}
        },
        {
            "file": "data/empty_table.pdf",
            "stage": "chunking",
            "error": "ValueError: Empty section content",
            "doc": {"title": "Empty Document"}
        }
    ]

    # 1. Persist DLQ
    count = persist_dead_letter_queue(fake_dlq, dlq_file_path=str(dlq_file))
    assert count == 2
    assert dlq_file.exists()

    # 2. Verify CRITICAL alert log emitted for Cloud Monitoring
    critical_logs = [rec.message for rec in caplog.records if rec.levelno == logging.CRITICAL]
    assert any("ALERT: DLQ_THRESHOLD_EXCEEDED" in msg for msg in critical_logs)
    assert any("2 document(s) failed ingestion" in msg for msg in critical_logs)

    # 3. Read back persisted records from disk
    restored = read_persisted_dead_letter_queue(dlq_file_path=str(dlq_file))
    assert len(restored) == 2
    assert restored[0]["file_path"] == "data/corrupted_manual.docx"
    assert restored[0]["stage"] == "parsing"
    assert "BadZipFile" in restored[0]["error_message"]
    assert restored[0]["doc_title"] == "Corrupted SOP Document"
    assert restored[0]["occurred_at"] is not None
    assert restored[1]["stage"] == "chunking"


def test_persist_dead_letter_queue_bigquery(monkeypatch, tmp_path):
    """
    P2.3: Verifies BigQuery table insertion for DLQ records when GCP project/dataset are specified.
    """
    from scripts.ingest.loaders import persist_dead_letter_queue

    mock_bq_client = MagicMock()
    mock_bq_client.insert_rows_json.return_value = []

    fake_dlq = [
        {
            "file": "data/bad_doc.md",
            "stage": "chunking",
            "error": "Invalid YAML header",
            "doc": {"title": "Broken Header"}
        }
    ]

    count = persist_dead_letter_queue(
        fake_dlq,
        project_id="test-corp-ai",
        dataset_id="it_helpdesk_kb",
        table_name="ingestion_dead_letter_queue",
        dlq_file_path=str(tmp_path / "dlq_bq.jsonl"),
        bq_client=mock_bq_client
    )

    assert count == 1
    assert mock_bq_client.insert_rows_json.called
    table_arg, rows_arg = mock_bq_client.insert_rows_json.call_args[0]
    assert table_arg == "test-corp-ai.it_helpdesk_kb.ingestion_dead_letter_queue"
    assert len(rows_arg) == 1
    assert rows_arg[0]["file_path"] == "data/bad_doc.md"
    assert rows_arg[0]["stage"] == "chunking"






