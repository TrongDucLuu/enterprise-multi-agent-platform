import pytest
from unittest.mock import MagicMock

from scripts.eval_harness import (
    evaluate_retrieval_precision_at_k,
    run_eval_suite,
    EVAL_DATASET,
)
from agent_core.tools.enterprise_rag_mcp.knowledge_store import InMemoryKnowledgeStore
from agent_core.tools.enterprise_rag_mcp.rag_models import SearchResult
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture(autouse=True)
def default_eval_sso_user():
    user = SSOUser(
        user_id="test-eval-user",
        email="eval@company.com",
        roles=["employee", "it_admin", "support_agent"],
        clearance_level=3,
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)


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


def test_mutation_inverted_ranking_fails_eval_quality_gate(monkeypatch):
    """
    🟠 P1.2 & P1.3 MUTATION TEST:
    With the expanded 30-article corpus (10 ERP, 10 HRM, 10 CRM),
    if search result ranking is inverted (e.g. reverse=False / lowest relevance first),
    the relevant articles are pushed out of top-k or down to lower ranks,
    causing Retrieval Precision@k to fall strictly below the 80% Quality Gate,
    and the eval suite reports ❌ FAIL.
    """
    # Mutate InMemoryKnowledgeStore.search to invert result sorting
    original_search = InMemoryKnowledgeStore.search

    def inverted_search(self, query: str, system: str = "ALL", limit: int = 3, **kwargs):
        results = original_search(self, query=query, system=system, limit=10, **kwargs)
        # Invert the ranking completely (worst match first)
        results.reverse()
        return results[:limit]

    monkeypatch.setattr(InMemoryKnowledgeStore, "search", inverted_search)

    summary, passed = run_eval_suite()
    hit_rate = summary["metrics"]["retrieval_hit_rate_percent"]
    mrr_score = summary["metrics"]["retrieval_mrr_score"]

    # Must fail the gate and overall suite must be FAILED
    assert hit_rate < 80.0 or mrr_score < 0.80, f"Expected hit rate < 80% or MRR < 0.80 under inverted ranking, got hit_rate={hit_rate}%, MRR={mrr_score}"
    assert passed is False, "Overall eval suite must FAIL when search ranking is inverted"
    assert summary["quality_gates"]["overall_status"] == "FAILED"


def test_eval_precision_is_strictly_rank_sensitive():
    """
    🟠 P1.3 RANK SENSITIVITY TEST:
    Simulates case where target ground truth is at Rank 1 vs Rank 3.
    Verifies that target at Rank 3 yields strictly lower MRR score (0.333) than Rank 1 (1.0),
    while Precision@k reflects the fraction of expected relevant items found in top-k.
    """
    class MockRankStore:
        def __init__(self, target_rank: int):
            self.target_rank = target_rank

        def search(self, query: str, system: str = "ERP", limit: int = 3, **kwargs):
            results = [
                SearchResult(
                    article_id=f"DISTRACTOR-00{i}",
                    system="ERP",
                    title=f"Distractor {i}",
                    snippet="...",
                    relevance_score=0.5,
                    source_uri=f"docs/d{i}.md",
                )
                for i in range(1, limit + 1)
            ]
            if 1 <= self.target_rank <= limit:
                results[self.target_rank - 1] = SearchResult(
                    article_id="ERP-KB-001",
                    system="ERP",
                    title="Correct Target Article",
                    snippet="...",
                    relevance_score=0.9,
                    source_uri="docs/erp_po_manual.md",
                )
            return results

    test_case = {
        "id": "TC-RANK-TEST",
        "query": "Lỗi PO M_BEST_EKO",
        "tier": "L2",
        "expected_system": "ERP",
        "expected_source_ids": ["ERP-KB-001"],
    }

    # Evaluate when target is at Rank 1
    res_rank_1 = evaluate_retrieval_precision_at_k(test_case, MockRankStore(target_rank=1), k=3)
    # Evaluate when target is at Rank 3
    res_rank_3 = evaluate_retrieval_precision_at_k(test_case, MockRankStore(target_rank=3), k=3)
    # Evaluate when target is not in top-k
    res_miss = evaluate_retrieval_precision_at_k(test_case, MockRankStore(target_rank=99), k=3)

    # Precision@3 = 1 / 3 = 0.333 for both hits (1 relevant doc retrieved out of 3)
    assert res_rank_1["precision_at_k"] == 0.333
    assert res_rank_1["mrr"] == 1.0
    assert res_rank_1["rank"] == 1

    assert res_rank_3["precision_at_k"] == 0.333
    assert res_rank_3["mrr"] == 0.333
    assert res_rank_3["rank"] == 3

    assert res_miss["precision_at_k"] == 0.0
    assert res_miss["mrr"] == 0.0
    assert res_miss["rank"] is None

    # Assert strict monotonic ordering for MRR: Rank 1 > Rank 3 > Miss
    assert res_rank_1["mrr"] > res_rank_3["mrr"] > res_miss["mrr"]

