"""
Unit tests for Vertex AI Reranker & Offline Cross-Field Ranker (Phase 1 Item B).
"""

import os
import pytest
from unittest.mock import patch, MagicMock

# Pins it-helpdesk pack for IT helpdesk knowledge articles
pytestmark = pytest.mark.usefixtures("pinned_it_helpdesk_pack")

from agent_core.app_utils.reranker import rerank_search_results, _fallback_cross_rerank
from agent_core.tools.enterprise_rag_mcp.knowledge.in_memory import InMemoryKnowledgeStore
from agent_core.tools.enterprise_rag_mcp.rag_models import SearchResult, SecurityContext


def test_fallback_cross_reranker_reordering():
    # Construct candidates where less relevant doc is initially at index 0
    cand1 = SearchResult(
        article_id="GEN-001",
        system="ERP",
        title="Thông tin chung về ERP",
        snippet="Hệ thống ERP hỗ trợ quản lý quy trình doanh nghiệp.",
        relevance_score=0.6,
    )
    cand2 = SearchResult(
        article_id="ERP-KB-001",
        system="ERP",
        title="Hướng dẫn tạo Purchase Order ME21N trong SAP ERP",
        snippet="Để tạo Purchase Order trong SAP ERP, sử dụng transaction ME21N.",
        relevance_score=0.5,
    )
    candidates = [cand1, cand2]

    reranked = _fallback_cross_rerank("Cách tạo Purchase Order ME21N", candidates, top_n=2)
    assert len(reranked) == 2
    # cand2 should be boosted to first position because of ME21N and Purchase Order title match
    assert reranked[0].article_id == "ERP-KB-001"


def test_rerank_search_results_disabled():
    cand1 = SearchResult(article_id="A1", system="ERP", title="T1", snippet="S1", relevance_score=0.9)
    cand2 = SearchResult(article_id="A2", system="ERP", title="T2", snippet="S2", relevance_score=0.8)
    candidates = [cand1, cand2]

    # Explicitly disabled
    res = rerank_search_results("query", candidates, top_n=2, use_reranker=False)
    assert [c.article_id for c in res] == ["A1", "A2"]


def test_rerank_search_results_offline_fallback():
    cand1 = SearchResult(article_id="A1", system="ERP", title="T1", snippet="S1", relevance_score=0.5)
    cand2 = SearchResult(article_id="A2", system="ERP", title="Khắc phục lỗi ME21N", snippet="S2", relevance_score=0.4)
    candidates = [cand1, cand2]

    # When offline and use_reranker=True, fallback cross ranker executes
    res = rerank_search_results("Lỗi ME21N", candidates, top_n=2, use_reranker=True)
    assert len(res) == 2
    assert res[0].article_id == "A2"


def test_in_memory_store_with_reranker_enabled():
    store = InMemoryKnowledgeStore()
    sec_ctx = SecurityContext.from_user(roles=["employee", "it_admin"], clearance_level=3)

    with patch("agent_core.tools.enterprise_rag_mcp.knowledge.in_memory.resolve_retrieval_config") as mock_cfg:
        mock_cfg.return_value = {
            "fraction_lists_to_search": 0.05,
            "hybrid_search_enabled": True,
            "reranker_enabled": True,
            "query_preprocessing_enabled": False,
            "query_rewrite_enabled": False,
        }
        results = store.search(
            query="Transaction ME21N Purchase Order",
            security_context=sec_ctx,
            system="ERP",
            limit=2,
        )
        assert len(results) > 0
        assert results[0].article_id == "ERP-KB-001"
