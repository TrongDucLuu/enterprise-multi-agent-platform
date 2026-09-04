"""
Unit tests for Corrective Retrieval Loop (Phase 1 Item C [R3]).
"""

import pytest
from unittest.mock import patch

# Pins it-helpdesk pack for IT helpdesk knowledge articles
pytestmark = pytest.mark.usefixtures("pinned_it_helpdesk_pack")

from agent_core.tools.enterprise_rag_mcp.rag_models import (
    KnowledgeArticle,
    SearchResult,
    SecurityContext,
)
from agent_core.tools.enterprise_rag_mcp.knowledge.in_memory import InMemoryKnowledgeStore
from agent_core.tools.enterprise_rag_mcp.knowledge.corrective_retriever import (
    evaluate_retrieval_confidence,
    refine_corrective_query,
    merge_candidate_results,
)


def test_evaluate_retrieval_confidence():
    """Test confidence evaluation against threshold."""
    # Empty results -> False
    assert evaluate_retrieval_confidence([]) is False

    # Low score results -> False
    low_res = [
        SearchResult(
            article_id="ART-01",
            system="ERP",
            title="Title",
            snippet="Snippet",
            relevance_score=0.45,
        )
    ]
    assert evaluate_retrieval_confidence(low_res, threshold=0.65) is False

    # High score results -> True
    high_res = [
        SearchResult(
            article_id="ART-02",
            system="ERP",
            title="Title",
            snippet="Snippet",
            relevance_score=0.85,
        )
    ]
    assert evaluate_retrieval_confidence(high_res, threshold=0.65) is True


def test_refine_corrective_query_preserves_codes_and_expands_synonyms():
    """Test that refine_corrective_query expands synonyms and preserves technical codes."""
    raw_query = "Làm sao để sửa lỗi kết nối SAP logon ME21N với ạ?"
    refined_r2 = refine_corrective_query(raw_query, round_index=2)
    
    # Check that technical code ME21N is preserved
    assert "ME21N" in refined_r2
    # Check that synonyms for 'lỗi' or 'kết nối' are included
    assert any(s in refined_r2.lower() for s in ["sự cố", "error", "connection", "connect", "logon"])

    refined_r3 = refine_corrective_query(raw_query, round_index=3)
    assert "ME21N" in refined_r3


def test_merge_candidate_results_deduplicates_and_sorts():
    """Test candidate merging retains highest relevance score and sorts descending."""
    cand1 = SearchResult(
        article_id="ART-01",
        chunk_index=0,
        system="ERP",
        title="Title 1",
        snippet="Snippet 1",
        relevance_score=0.5,
    )
    cand2 = SearchResult(
        article_id="ART-02",
        chunk_index=0,
        system="ERP",
        title="Title 2",
        snippet="Snippet 2",
        relevance_score=0.7,
    )
    # Updated cand1 with higher score
    cand1_better = SearchResult(
        article_id="ART-01",
        chunk_index=0,
        system="ERP",
        title="Title 1",
        snippet="Snippet 1",
        relevance_score=0.9,
    )

    merged = merge_candidate_results([cand1, cand2], [cand1_better], limit=3)
    assert len(merged) == 2
    assert merged[0].article_id == "ART-01"
    assert merged[0].relevance_score == 0.9
    assert merged[1].article_id == "ART-02"
    assert merged[1].relevance_score == 0.7


def test_in_memory_store_corrective_retrieval_integration():
    """Test in-memory store executes corrective loop when enabled and confidence is initially low."""
    article_erp = KnowledgeArticle(
        id="ERP-KB-001",
        system="ERP",
        title="Hướng dẫn xử lý lỗi kết nối SAP GUI và SAP Logon",
        content="Hướng dẫn xử lý sự cố lỗi kết nối SAP GUI connection error ME21N.",
        category="troubleshooting",
        keywords=["SAP", "connection", "error", "ME21N", "logon"],
        allowed_roles=["*"],
    )
    store = InMemoryKnowledgeStore([article_erp])
    sec_ctx = SecurityContext(authenticated=True, user_id="user@company.com", roles=["*"], clearance_level=1)

    # 1. Without corrective retrieval
    cfg_disabled = {
        "hybrid_search_enabled": True,
        "reranker_enabled": False,
        "corrective_retrieval_enabled": False,
        "adaptive_retrieval_rounds": 1,
    }
    with patch("agent_core.tools.enterprise_rag_mcp.knowledge.in_memory.resolve_retrieval_config", return_value=cfg_disabled):
        res_dis = store.search("ME21N", security_context=sec_ctx)
        assert len(res_dis) == 1
        assert res_dis[0].article_id == "ERP-KB-001"

    # 2. With corrective retrieval enabled
    cfg_enabled = {
        "hybrid_search_enabled": True,
        "reranker_enabled": False,
        "corrective_retrieval_enabled": True,
        "adaptive_retrieval_rounds": 2,
        "confidence_threshold": 0.65,
    }
    with patch("agent_core.tools.enterprise_rag_mcp.knowledge.in_memory.resolve_retrieval_config", return_value=cfg_enabled):
        # Query with low direct match on title, but recoverable via corrective refinement
        res_corr = store.search("Làm thế nào sửa sự cố ME21N ạ?", security_context=sec_ctx)
        assert len(res_corr) >= 1
        assert res_corr[0].article_id == "ERP-KB-001"


def test_corrective_retrieval_strictly_preserves_rbac():
    """Test that unauthorized documents are never returned during corrective retrieval rounds."""
    restricted_article = KnowledgeArticle(
        id="SEC-KB-099",
        system="ERP",
        title="Quy trình bảo mật tài chính tối mật",
        content="Nội dung bảo mật tối mật chỉ dành cho finance_admin ME21N.",
        category="security",
        keywords=["security", "finance", "ME21N"],
        allowed_roles=["finance_admin"],
        clearance_level=3,
    )
    store = InMemoryKnowledgeStore([restricted_article])
    
    # Normal user without finance_admin role or clearance
    user_sec_ctx = SecurityContext(authenticated=True, user_id="guest@company.com", roles=["employee"], clearance_level=1)

    cfg_enabled = {
        "hybrid_search_enabled": True,
        "reranker_enabled": False,
        "corrective_retrieval_enabled": True,
        "adaptive_retrieval_rounds": 3,
        "confidence_threshold": 0.8,
    }
    with patch("agent_core.tools.enterprise_rag_mcp.knowledge.in_memory.resolve_retrieval_config", return_value=cfg_enabled):
        results = store.search("Quy trình bảo mật tài chính tối mật ME21N", security_context=user_sec_ctx)
        # Must be empty because document fails authorization!
        assert len(results) == 0
