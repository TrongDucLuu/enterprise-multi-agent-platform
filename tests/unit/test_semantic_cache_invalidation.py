import pytest
from agent_core.app_utils.semantic_cache import InMemorySemanticCache


def test_inmemory_semantic_cache_invalidation():
    cache = InMemorySemanticCache(embedding_fn=lambda q: [1.0, 0.0, 0.0] * 42 + [0.0] * 2)
    cache.set(
        query="lỗi ME21N tạo đơn hàng",
        response="Hướng dẫn chi tiết xử lý lỗi ME21N: [Mã: ERP-001]",
        is_public=True,
        tier="L2",
        metadata={"article_id": "ERP-001", "system": "ERP"},
    )
    cache.set(
        query="hướng dẫn chấm công",
        response="Quy trình chấm công Workday: [Mã: HRM-001]",
        is_public=True,
        tier="L2",
        metadata={"article_id": "HRM-001", "system": "HRM"},
    )

    assert len(cache._entries) == 2

    # Invalidate article ERP-001
    removed = cache.invalidate(article_id="ERP-001")
    assert removed == 1
    assert len(cache._entries) == 1
    assert cache._entries[0].metadata.get("article_id") == "HRM-001"

    # Invalidate system HRM
    removed_sys = cache.invalidate(system="HRM")
    assert removed_sys == 1
    assert len(cache._entries) == 0


def test_reconcile_deleted_documents_triggers_cache_invalidation_mutation(monkeypatch):
    """
    🟠 P1 MUTATION TEST:
    Verifies that running reconcile_deleted_documents() (which tombstones an article)
    automatically invalidates all related semantic cache entries without manual cache.invalidate() calls.
    """
    from unittest.mock import MagicMock
    from scripts.ingest.loaders import reconcile_deleted_documents
    import agent_core.app_utils.semantic_cache as sem_cache_mod

    # Setup real in-memory semantic cache instance with orthogonal embeddings for different domains
    def domain_embedding_fn(q: str) -> list[float]:
        if "ERP" in q or "ME21N" in q:
            return [1.0] + [0.0] * 127
        return [0.0] * 64 + [1.0] + [0.0] * 63

    real_cache = InMemorySemanticCache(embedding_fn=domain_embedding_fn)
    real_cache.set(
        query="Lỗi đơn hàng ERP ME21N",
        response="Hướng dẫn xử lý lỗi ME21N [Tài liệu: ERP-001]",
        is_public=True,
        tier="L2",
        metadata={"article_id": "ERP-001", "system": "ERP"}
    )
    real_cache.set(
        query="Chấm công nhân sự HRM",
        response="Hướng dẫn chấm công [Tài liệu: HRM-001]",
        is_public=True,
        tier="L2",
        metadata={"article_id": "HRM-001", "system": "HRM"}
    )
    assert len(real_cache._entries) == 2

    # Patch get_semantic_cache to return our instance
    monkeypatch.setattr(sem_cache_mod, "get_semantic_cache", lambda: real_cache)

    mock_bq = MagicMock()
    # 1. First query: UPDATE tombstone SQL
    mock_tombstone_job = MagicMock()
    mock_tombstone_job.num_dml_affected_rows = 1
    
    # 2. Second query: SELECT DISTINCT id, parent_doc_id tombstoned rows
    mock_find_job = MagicMock()
    row_tombstoned = MagicMock()
    row_tombstoned.id = "ERP-001-c0"
    row_tombstoned.parent_doc_id = "ERP-001"
    row_tombstoned.system = "ERP"
    mock_find_job.result.return_value = [row_tombstoned]

    mock_bq.query.side_effect = [mock_tombstone_job, mock_find_job]

    # Run actual reconcile_deleted_documents
    reconciled = reconcile_deleted_documents(
        bq_client=mock_bq,
        project_id="test-proj",
        dataset_id="test_kb",
        table_name="knowledge_articles",
        active_source_uris=["data/hrm.md"]
    )

    assert reconciled == 1
    # Cache for ERP-001 MUST be immediately cleared by reconcile_deleted_documents
    assert len(real_cache._entries) == 1
    assert real_cache._entries[0].metadata.get("article_id") == "HRM-001"

    # Search for ERP-001 query returns None (cache miss)
    hit = real_cache.get("Lỗi đơn hàng ERP ME21N", tier="L2")
    assert hit is None


def test_ingest_articles_triggers_cache_invalidation_on_update_mutation(monkeypatch):
    """
    🟠 P1 MUTATION TEST:
    Verifies that updating/ingesting documents via ingest_articles_to_bigquery()
    automatically invalidates the old cached answers for the updated document IDs.
    """
    from unittest.mock import MagicMock
    from scripts.ingest.loaders import ingest_articles_to_bigquery
    import agent_core.app_utils.semantic_cache as sem_cache_mod

    real_cache = InMemorySemanticCache(embedding_fn=lambda q: [1.0, 0.0, 0.0] * 42 + [0.0] * 2)
    real_cache.set(
        query="Cách tạo báo cáo tài chính",
        response="Bản hướng dẫn cũ [Tài liệu: FIN-001]",
        is_public=True,
        tier="L2",
        metadata={"article_id": "FIN-001", "system": "FIN"}
    )
    assert len(real_cache._entries) == 1

    monkeypatch.setattr(sem_cache_mod, "get_semantic_cache", lambda: real_cache)

    mock_bq_module = MagicMock()
    mock_client = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.num_dml_affected_rows = 1
    mock_client.query.return_value = mock_query_job
    mock_bq_module.Client.return_value = mock_client
    monkeypatch.setattr("google.cloud.bigquery", mock_bq_module, raising=False)

    # Ingest updated FIN-001
    updated_articles = [{
        "id": "FIN-001-c0",
        "parent_doc_id": "FIN-001",
        "system": "FIN",
        "title": "Báo cáo tài chính v2",
        "category": "Accounting",
        "content": "Nội dung cập nhật mới 2026...",
        "keywords": ["finance", "report"],
        "source_uri": "data/finance.md",
        "content_hash": "hash123",
        "updated_at": "2026-09-01T00:00:00Z"
    }]

    ingest_articles_to_bigquery(
        updated_articles,
        project_id="test-proj",
        dataset_id="test_kb",
        table_name="knowledge_articles"
    )

    # Cache for FIN-001 MUST be immediately cleared upon MERGE
    assert len(real_cache._entries) == 0
    hit = real_cache.get("Cách tạo báo cáo tài chính", tier="L2")
    assert hit is None

