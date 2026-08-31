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
    assert "STORING (system, category, id, title, content, section_hierarchy)" in ddl_call


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

