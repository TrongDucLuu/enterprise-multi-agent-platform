import os
from unittest.mock import MagicMock
import pytest
from it_helpdesk_agent.tools.enterprise_rag_mcp.knowledge_store import (
    BaseKnowledgeStore,
    InMemoryKnowledgeStore,
    BigQueryVectorKnowledgeStore,
    get_knowledge_store,
    KnowledgeArticle,
)


def test_in_memory_knowledge_store_search_and_get():
    store = InMemoryKnowledgeStore()
    assert isinstance(store, BaseKnowledgeStore)

    # Search ERP
    results = store.search("Purchase Order phân quyền", system="ERP", limit=2)
    assert len(results) > 0
    assert results[0].system == "ERP"
    assert "ERP-KB-001" == results[0].article_id

    # Get by ID
    article = store.get_article_by_id("ERP-KB-001")
    assert article is not None
    assert "SAP/Oracle" in article.title

    # Not found
    assert store.get_article_by_id("NON-EXISTENT") is None


def test_bigquery_vector_store_with_mock_client():
    mock_bq = MagicMock()
    mock_row = MagicMock()
    mock_row.id = "ERP-KB-001"
    mock_row.system = "ERP"
    mock_row.title = "Khắc phục lỗi PO"
    mock_row.content = "Nội dung chi tiết về phân quyền Purchase Order..."
    mock_row.section_hierarchy = {"h1": "ERP Guide", "h2": "Procurement", "h3": "PO Authorization"}
    mock_row.distance = 0.15

    mock_query_job = MagicMock()
    mock_query_job.result.return_value = [mock_row]
    mock_bq.query.return_value = mock_query_job

    def mock_embed(text: str) -> list[float]:
        return [0.1] * 64

    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=mock_embed
    )

    # 1. Test search with system="ERP" (Pre-filter subquery)
    results = store.search("Lỗi tạo PO", system="ERP", limit=1)
    assert len(results) == 1
    assert results[0].article_id == "ERP-KB-001"
    assert results[0].relevance_score == 0.85
    assert results[0].section_hierarchy.h1 == "ERP Guide"
    assert results[0].section_hierarchy.h2 == "Procurement"
    assert results[0].context_path == "ERP Guide > Procurement > PO Authorization"
    assert mock_bq.query.called

    sql_arg = mock_bq.query.call_args[0][0]
    # Verify pre-filtering subquery in VECTOR_SEARCH first argument
    assert "FROM VECTOR_SEARCH(" in sql_arg
    assert "WHERE system = @system_param" in sql_arg
    assert "fraction_lists_to_search" in sql_arg

    # 2. Test search with system="HRM"
    store.search("Chấm công", system="HRM", limit=3)
    sql_arg_hrm = mock_bq.query.call_args[0][0]
    assert "WHERE system = @system_param" in sql_arg_hrm

    # 3. Test search with system="ALL" and RBAC allowed_systems (Pre-filter with allowed systems)
    store.search("Reset password", system="ALL", limit=5, allowed_systems=["ERP", "HRM"])
    sql_arg_all = mock_bq.query.call_args[0][0]
    assert "WHERE system IN UNNEST(@allowed_systems_param)" in sql_arg_all

    # 4. Test Index DDL contains STORING clause
    mock_bq.reset_mock()
    from scripts.ingest_knowledge_base import ensure_vector_index
    ensure_vector_index(mock_bq, project_id="test-project", dataset_id="test_kb", table_name="articles")
    ddl_call = mock_bq.query.call_args[0][0]
    assert "STORING (system, category, id, title, content, section_hierarchy, source_uri, owner, effective_date, expiry_date, is_deleted)" in ddl_call


def test_in_memory_knowledge_store_section_hierarchy():
    store = InMemoryKnowledgeStore()
    results = store.search("Purchase Order", system="ERP", limit=1)
    assert len(results) > 0
    assert results[0].section_hierarchy is not None
    assert results[0].section_hierarchy.h1 == "Tài liệu ERP"
    assert "Tài liệu ERP" in results[0].context_path


def test_get_knowledge_store_factory(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "in_memory")
    store = get_knowledge_store()
    assert isinstance(store, InMemoryKnowledgeStore)

    monkeypatch.setenv("KNOWLEDGE_BACKEND", "bigquery")
    store_bq = get_knowledge_store()
    assert isinstance(store_bq, BigQueryVectorKnowledgeStore)


def test_hybrid_search_boosts_exact_transaction_codes():
    """
    P2.6 Hybrid Search:
    Verifies that exact transaction codes (M_BEST_EKO, ME21N, OB52) receive relevance boosts
    and rank at position 1.
    """
    store = InMemoryKnowledgeStore()

    # Query with exact authorization object M_BEST_EKO
    results_eko = store.search("Không thể duyệt PO do mã M_BEST_EKO", system="ERP", limit=3)
    assert len(results_eko) > 0
    assert results_eko[0].article_id == "ERP-KB-001"
    assert results_eko[0].relevance_score >= 0.8

    # Query with transaction code OB52
    results_ob52 = store.search("Lỗi kỳ kế toán đóng OB52", system="ERP", limit=3)
    assert len(results_ob52) > 0
    assert results_ob52[0].article_id == "ERP-KB-002"
    assert results_ob52[0].relevance_score >= 0.8


def test_content_governance_date_filtering():
    """
    P2.7 Content Governance Metadata:
    Verifies that expired documents (expiry_date < today) and future documents (effective_date > today)
    are excluded from retrieval.
    """
    # Create isolated store
    expired_article = KnowledgeArticle(
        id="ERP-EXPIRED-999",
        system="ERP",
        title="Quy trình Mua hàng 2020 Cũ",
        category="Procurement",
        content="Quy trình mua hàng cũ hết hiệu lực...",
        keywords=["sap", "po", "procurement"],
        source_uri="docs/old_procurement.md",
        effective_date="2020-01-01",
        expiry_date="2021-01-01",  # Expired
        is_deleted=False,
    )

    future_article = KnowledgeArticle(
        id="ERP-FUTURE-999",
        system="ERP",
        title="Quy trình Mua hàng 2030 Tương lai",
        category="Procurement",
        content="Quy trình mua hàng tương lai chưa áp dụng...",
        keywords=["sap", "po", "future"],
        source_uri="docs/future_procurement.md",
        effective_date="2035-01-01",  # Future
        expiry_date=None,
        is_deleted=False,
    )

    store = InMemoryKnowledgeStore(articles=[expired_article, future_article])

    # Search: Neither expired nor future articles should appear
    results = store.search("Quy trình mua hàng", system="ERP")
    retrieved_ids = [r.article_id for r in results]
    assert "ERP-EXPIRED-999" not in retrieved_ids
    assert "ERP-FUTURE-999" not in retrieved_ids


def test_search_result_metadata_propagation():
    """
    P1.3 Metadata Flow & Traceability:
    Verifies that SearchResult contains valid source_uri, category, keywords, owner, effective_date,
    and does NOT default to 'built-in' or null when metadata exists in store.
    """
    store = InMemoryKnowledgeStore()
    results = store.search("Purchase Order phân quyền", system="ERP", limit=1)
    assert len(results) == 1
    sr = results[0]
    assert sr.source_uri == "docs/erp_po_manual.md"
    assert sr.source_uri != "built-in"
    assert sr.source_uri is not None
    assert "Procurement" in sr.category
    assert "sap" in sr.keywords
    assert sr.owner == "erp-team@company.com"
    assert sr.effective_date == "2025-01-01"
    assert sr.is_deleted is False


def test_mutation_removing_expiry_date_filter_leaks_expired_policy():
    """
    MUTATION TEST:
    If expiry_date pre-filter is omitted, expired documents leak into search results (RED).
    """
    expired_article = KnowledgeArticle(
        id="EXPIRED_TAX_GUIDE",
        system="ERP",
        title="Biểu thuế VAT 2021 Hết hiệu lực",
        category="Finance",
        content="Hướng dẫn kê khai thuế VAT 10% cho mặt hàng ưu đãi năm 2021...",
        source_uri="docs/expired_tax.md",
        effective_date="2021-01-01",
        expiry_date="2021-12-31",
        is_deleted=False,
    )
    store = InMemoryKnowledgeStore(articles=[expired_article])

    # With proper filtering, returns 0 results
    clean_results = store.search("Biểu thuế VAT", system="ERP")
    assert "EXPIRED_TAX_GUIDE" not in [r.article_id for r in clean_results]


def test_mutation_removing_source_uri_breaks_citation_integrity():
    """
    MUTATION TEST:
    If source_uri is missing from SearchResult, downstream agent citations cannot be grounded.
    """
    store = InMemoryKnowledgeStore()
    results = store.search("Timesheet", system="HRM", limit=1)
    assert len(results) > 0
    assert results[0].source_uri is not None
    assert results[0].source_uri == "docs/hrm_timesheet_sync.md"

