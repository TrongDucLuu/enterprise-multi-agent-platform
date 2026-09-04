"""
Unit tests for Query Processor (Non-LLM Preprocessing & LLM Rewrite).
"""

import os
import time
import pytest
from unittest.mock import patch, MagicMock

from agent_core.tools.enterprise_rag_mcp.knowledge.query_processor import (
    preprocess_query,
    rewrite_query_with_llm,
    process_retrieval_query,
)
from agent_core.tools.enterprise_rag_mcp.knowledge.in_memory import InMemoryKnowledgeStore
from agent_core.tools.enterprise_rag_mcp.rag_models import SecurityContext, KnowledgeArticle


def test_preprocess_query_vietnamese_conversational():
    raw_q1 = "Làm sao để cấu hình VPN trên macOS?"
    processed_q1 = preprocess_query(raw_q1)
    assert "cấu hình VPN trên macOS" in processed_q1
    assert "Làm sao để" not in processed_q1

    raw_q2 = "Vui lòng cho tôi hỏi cách khắc phục lỗi 504 Gateway Timeout là gì?"
    processed_q2 = preprocess_query(raw_q2)
    assert "504" in processed_q2
    assert "Gateway Timeout" in processed_q2
    assert "Vui lòng cho tôi hỏi" not in processed_q2
    assert "là gì" not in processed_q2


def test_preprocess_query_english_conversational():
    raw_q1 = "How do I fix transaction ME21N purchase order error?"
    processed_q1 = preprocess_query(raw_q1)
    assert "ME21N" in processed_q1
    assert "purchase order" in processed_q1
    assert "How do I" not in processed_q1

    raw_q2 = "Can you please tell me where can I find article HDW-KB-001?"
    processed_q2 = preprocess_query(raw_q2)
    assert "HDW-KB-001" in processed_q2
    assert "Can you please tell me" not in processed_q2


def test_preprocess_query_preserves_technical_codes():
    codes = ["ME21N", "OB52", "M_BEST_EKO", "HDW-KB-001", "IT-POL-003", "504", "403", "OOM"]
    for code in codes:
        query = f"Hướng dẫn xử lý lỗi {code} trong hệ thống ERP"
        res = preprocess_query(query)
        assert code in res, f"Failed to preserve technical code {code} in '{res}'"


def test_preprocess_query_edge_cases():
    assert preprocess_query("") == ""
    assert preprocess_query("   ") == ""
    assert preprocess_query(None) == ""
    # Only stop phrases
    res = preprocess_query("là gì")
    assert res == "là gì"


def test_rewrite_query_offline_fallback():
    with patch.dict(os.environ, {"USE_VERTEX_EMBEDDING": "false", "GOOGLE_CLOUD_PROJECT": "", "PROJECT_ID": ""}):
        res = rewrite_query_with_llm("Làm sao để đổi mật khẩu VPN?")
        assert "đổi mật khẩu VPN" in res


def test_rewrite_query_mocked_success():
    with patch.dict(os.environ, {"USE_VERTEX_EMBEDDING": "true", "GOOGLE_CLOUD_PROJECT": "mock-proj"}):
        with patch("agent_core.tools.enterprise_rag_mcp.knowledge.query_processor._invoke_llm_rewrite_sync") as mock_call:
            mock_call.return_value = "VPN password reset procedure macOS Windows"
            res = rewrite_query_with_llm("Hướng dẫn đổi pass VPN")
            assert res == "VPN password reset procedure macOS Windows"


def test_rewrite_query_timeout_fallback():
    with patch.dict(os.environ, {"USE_VERTEX_EMBEDDING": "true", "GOOGLE_CLOUD_PROJECT": "mock-proj"}):
        with patch("agent_core.tools.enterprise_rag_mcp.knowledge.query_processor._invoke_llm_rewrite_sync") as mock_call:
            def slow_call(*args, **kwargs):
                time.sleep(0.5)
                return "Slow Result"
            mock_call.side_effect = slow_call
            # Set tiny timeout of 0.05s
            res = rewrite_query_with_llm("Làm sao để mở khóa tài khoản SAP?", timeout=0.05)
            # Must fall back gracefully to preprocessed query
            assert "mở khóa tài khoản SAP" in res
            assert res != "Slow Result"


def test_process_retrieval_query_flags():
    # 1. Disabled (default)
    cfg_disabled = {"query_preprocessing_enabled": False, "query_rewrite_enabled": False}
    assert process_retrieval_query("  Làm sao để mở cổng 443?  ", cfg_disabled) == "Làm sao để mở cổng 443?"

    # 2. Preprocessing enabled
    cfg_prep = {"query_preprocessing_enabled": True, "query_rewrite_enabled": False}
    res_prep = process_retrieval_query("Làm sao để mở cổng 443?", cfg_prep)
    assert "mở cổng 443" in res_prep
    assert "Làm sao để" not in res_prep

    # 3. Rewrite enabled (fallback to prep when offline)
    with patch.dict(os.environ, {"USE_VERTEX_EMBEDDING": "false", "GOOGLE_CLOUD_PROJECT": ""}):
        cfg_rewrite = {"query_preprocessing_enabled": False, "query_rewrite_enabled": True}
        res_rewrite = process_retrieval_query("Làm sao để mở cổng 443?", cfg_rewrite)
        assert "mở cổng 443" in res_rewrite


def test_in_memory_store_with_query_preprocessing():
    store = InMemoryKnowledgeStore()
    sec_ctx = SecurityContext.from_user(roles=["employee", "it_admin"], clearance_level=3)

    # Search with conversational noise and query preprocessing enabled
    with patch("agent_core.tools.enterprise_rag_mcp.knowledge.in_memory.resolve_retrieval_config") as mock_cfg:
        mock_cfg.return_value = {
            "fraction_lists_to_search": 0.05,
            "hybrid_search_enabled": True,
            "reranker_enabled": False,
            "query_preprocessing_enabled": True,
            "query_rewrite_enabled": False,
        }
        results = store.search(
            query="Vui lòng cho tôi hỏi làm sao để tạo Purchase Order ME21N trong ERP?",
            security_context=sec_ctx,
            system="ERP",
            limit=3,
        )
        assert len(results) >= 1
        assert results[0].article_id == "ERP-KB-001"
