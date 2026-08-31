import pytest
from unittest.mock import MagicMock

from scripts.eval_harness import (
    evaluate_retrieval_precision_at_k,
    run_eval_suite,
    EVAL_DATASET,
)
from it_helpdesk_agent.tools.enterprise_rag_mcp.knowledge_store import InMemoryKnowledgeStore
from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import SearchResult


def test_eval_retrieval_precision_at_k_normal_flow():
    """
    Tests evaluate_retrieval_precision_at_k with genuine InMemoryKnowledgeStore.
    Asserts expected source IDs are retrieved for standard L2 test cases.
    """
    store = InMemoryKnowledgeStore()
    
    # Case with ERP expected source
    test_case_erp = {
        "id": "TC-EVAL-001",
        "query": "Lỗi PO không thể phê duyệt do thiếu authorization object M_BEST_EKO",
        "tier": "L2",
        "expected_system": "ERP",
        "expected_source_ids": ["ERP-KB-001"],
    }
    res = evaluate_retrieval_precision_at_k(test_case_erp, store, k=3)
    assert res["applicable"] is True
    assert res["hit"] is True
    assert "ERP-KB-001" in res["retrieved_ids"]
    assert res["precision_at_k"] > 0.0

    # Non-applicable case (no expected_source_ids)
    test_case_l1 = {
        "id": "TC-EVAL-002",
        "query": "Reset mật khẩu",
        "tier": "L1",
        "expected_system": "IAM",
        "expected_source_ids": [],
    }
    res_l1 = evaluate_retrieval_precision_at_k(test_case_l1, store, k=3)
    assert res_l1["applicable"] is False


def test_mutation_retrieval_precision_gate_fails_when_search_is_corrupted(monkeypatch):
    """
    MUTATION TEST:
    If search retrieval returns wrong / irrelevant articles,
    Retrieval Precision@k drops below 80% and the overall evaluation suite FAILS (RED).
    """
    corrupted_store = InMemoryKnowledgeStore()
    
    # Mutate search to return completely unrelated results
    def fake_corrupted_search(*args, **kwargs):
        return [
            SearchResult(
                article_id="CORRUPTED-JUNK-999",
                system="ERP",
                title="Garbage Article",
                snippet="Completely irrelevant text...",
                relevance_score=0.1,
                source_uri="docs/corrupted.md",
            )
        ]

    corrupted_store.search = fake_corrupted_search

    # Evaluate all L2 cases with corrupted store
    hits = 0
    total = 0
    for case in EVAL_DATASET:
        if case.get("expected_source_ids"):
            total += 1
            res = evaluate_retrieval_precision_at_k(case, corrupted_store, k=3)
            if res["hit"]:
                hits += 1

    precision_pct = (hits / total) * 100
    assert precision_pct == 0.0  # 0% precision
    assert precision_pct < 80.0  # Fails the 80% quality gate!
