"""
Unit tests for Chunking Strategies, Token Estimation, and Benchmark Suite (Phase 1 Item D [R2]).
"""

import pytest
from scripts.ingest.chunkers import (
    estimate_tokens,
    is_well_structured,
    chunk_by_sections,
    chunk_text,
    process_document,
    benchmark_chunking_configurations,
)

# Pins it-helpdesk pack for valid systems and config
pytestmark = pytest.mark.usefixtures("pinned_it_helpdesk_pack")


def test_estimate_tokens():
    empty_tokens = estimate_tokens("")
    assert empty_tokens == 0

    en_text = "This is a standard English technical guide for database configuration."
    en_tokens = estimate_tokens(en_text)
    assert en_tokens > 5

    vi_text = "Hướng dẫn chi tiết xử lý lỗi phân quyền và khóa kỳ kế toán trên SAP ERP."
    vi_tokens = estimate_tokens(vi_text)
    assert vi_tokens > 10


def test_is_well_structured_sections():
    # Empty or single section -> False
    assert not is_well_structured([])
    assert not is_well_structured([{"heading": "H1", "content": "Too short"}])

    # Unbalanced section (>65% in one section)
    unbalanced = [
        {"heading": "H1", "content": "Short intro"},
        {"heading": "H2", "content": "A" * 1000},
    ]
    assert not is_well_structured(unbalanced, max_section_ratio=0.65)

    # Well structured multi-section
    balanced = [
        {"heading": "H1", "content": "Nội dung phần một chi tiết quy trình " * 10},
        {"heading": "H2", "content": "Nội dung phần hai chi tiết khắc phục " * 10},
        {"heading": "H3", "content": "Nội dung phần ba chi tiết xác nhận " * 10},
    ]
    assert is_well_structured(balanced, max_section_ratio=0.65, min_avg_length=50)


def test_chunk_by_sections_preserves_headings_and_hierarchy():
    sections = [
        {
            "heading": "Cấu Hình SAP",
            "content": "Chi tiết kết nối cổng 3200.",
            "hierarchy": {"h1": "Sổ Tay ERP", "h2": "Cấu Hình SAP", "h3": None},
        },
        {
            "heading": "Khắc Phục Lỗi",
            "content": "Quy trình xóa lock entries trong SM12.",
            "hierarchy": {"h1": "Sổ Tay ERP", "h2": "Khắc Phục Lỗi", "h3": None},
        },
    ]

    chunks_with_meta = chunk_by_sections(sections, max_chunk_size=500, return_metadata=True)
    assert len(chunks_with_meta) == 2
    assert "## Cấu Hình SAP" in chunks_with_meta[0]["text"]
    assert chunks_with_meta[0]["hierarchy"]["h2"] == "Cấu Hình SAP"
    assert "## Khắc Phục Lỗi" in chunks_with_meta[1]["text"]


def test_chunk_text_protects_code_blocks():
    text_with_code = (
        "Giới thiệu cấu hình kernel:\n\n"
        "```bash\n"
        "sapcontrol -nr 00 -function GetProcessList\n"
        "sapcontrol -nr 00 -function Stop\n"
        "sapcontrol -nr 00 -function Start\n"
        "```\n\n"
        "Sau khi khởi động lại, kiểm tra trạng thái SM51."
    )

    chunks = chunk_text(text_with_code, max_chunk_size=400, overlap=50, protect_code_blocks=True)
    assert len(chunks) >= 1
    # Check that code fence is intact in the containing chunk
    code_chunk = [c for c in chunks if "sapcontrol" in c][0]
    assert code_chunk.count("```") == 2


def test_chunk_text_protects_markdown_tables():
    text_with_table = (
        "Bảng tra cứu mã lỗi:\n\n"
        "| Mã Lỗi | Mô Tả | Xử Lý |\n"
        "|---|---|---|\n"
        "| ERR_01 | Deadlock | Kill lock |\n"
        "| ERR_02 | OOM | Tăng RAM |\n\n"
        "Ghi chú: Liên hệ DBA nếu lỗi tiếp diễn."
    )

    chunks = chunk_text(text_with_table, max_chunk_size=300, overlap=30, protect_tables=True)
    assert len(chunks) >= 1
    table_chunk = [c for c in chunks if "| Mã Lỗi |" in c][0]
    assert "| ERR_01 |" in table_chunk
    assert "| ERR_02 |" in table_chunk


def test_benchmark_chunking_configurations():
    sample_docs = [
        {
            "id": "DOC-01",
            "system": "ERP",
            "title": "Hướng dẫn SAP ERP",
            "content": "Nội dung hướng dẫn xử lý lỗi SAP ERP...\n\n```python\nprint('db connection')\n```\n\nKết thúc.",
            "sections": [
                {"heading": "Cài đặt", "content": "Nội dung cài đặt SAP...\n\n```python\nprint('db connection')\n```"},
                {"heading": "Vận hành", "content": "Nội dung vận hành SAP chi tiết."},
            ],
        }
    ]

    bench_results = benchmark_chunking_configurations(sample_docs)
    assert len(bench_results) == 6
    for r in bench_results:
        assert "config_name" in r
        assert "strategy" in r
        assert r["total_chunks"] >= 1
        assert r["avg_chars"] > 0
        assert r["code_block_integrity_pct"] >= 0.0


def test_process_document_deterministic_generation():
    doc = {
        "title": "Quy trình xin nghỉ phép HRM",
        "system": "HRM",
        "category": "Policy",
        "content": "Nhân viên gửi đơn xin nghỉ qua portal trước 3 ngày làm việc.",
        "source_uri": "gs://bucket/hrm_leave.md",
        "allowed_roles": ["employee", "hr_manager"],
        "sensitivity": "INTERNAL",
    }

    articles_1 = process_document(doc)
    articles_2 = process_document(doc)

    assert len(articles_1) == 1
    assert len(articles_2) == 1
    assert articles_1[0]["id"] == articles_2[0]["id"]
    assert articles_1[0]["content_hash"] == articles_2[0]["content_hash"]
    assert articles_1[0]["system"] == "HRM"
    assert articles_1[0]["allowed_roles"] == ["employee", "hr_manager"]
