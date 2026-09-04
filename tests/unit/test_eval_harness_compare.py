"""
Unit tests for Eval Harness A/B Comparison and Configuration Tracking
===================================================================
Tests configuration extraction, metric deltas, category diffs, and case regression tracking.
"""

import json
import pytest
from scripts.eval_harness import (
    extract_eval_configuration,
    compare_and_print_reports,
    run_offline_eval_suite,
    load_eval_dataset,
)
from agent_core.tools.enterprise_rag_mcp.knowledge.in_memory import InMemoryKnowledgeStore


def test_extract_eval_configuration():
    store = InMemoryKnowledgeStore()
    cfg = extract_eval_configuration(store=store, domain_pack="it-helpdesk", seed=42, k=5)
    assert cfg["domain_pack"] == "it-helpdesk"
    assert cfg["seed"] == 42
    assert cfg["final_k"] == 5
    assert "hybrid_search_enabled" in cfg
    assert "reranker_enabled" in cfg
    assert "query_preprocessing_enabled" in cfg
    assert "query_rewrite_enabled" in cfg
    assert "corrective_retrieval_enabled" in cfg
    assert cfg["num_kb_chunks"] >= 0


def test_category_breakdown_and_latency(monkeypatch):
    monkeypatch.setenv("DOMAIN_PACK", "it-helpdesk")
    store = InMemoryKnowledgeStore()
    dataset = load_eval_dataset(limit=20, seed=123)
    summary, passed = run_offline_eval_suite(eval_dataset=dataset, store=store, k=3, seed=123)

    assert "category_metrics" in summary
    assert "happy_path" in summary["category_metrics"] or "rbac" in summary["category_metrics"]
    assert "retrieval_latency_p50_ms" in summary["metrics"]
    assert "retrieval_latency_p95_ms" in summary["metrics"]
    assert summary["metrics"]["retrieval_latency_p50_ms"] >= 0.0


def test_compare_and_print_reports(tmp_path, capsys):
    baseline_data = {
        "timestamp": "2026-09-04 10:00:00 UTC",
        "total_test_cases": 2,
        "configuration": {
            "reranker_enabled": False,
            "final_k": 3,
        },
        "metrics": {
            "keyword_baseline_accuracy_percent": 90.0,
            "retrieval_hit_rate_percent": 90.0,
            "retrieval_mrr_score": 0.85,
            "retrieval_latency_p50_ms": 5.0,
            "rbac_compliance_rate_percent": 100.0,
            "total_llm_calls_executed": 0,
        },
        "category_metrics": {
            "happy_path": {"total_cases": 2, "passed_cases": 2, "pass_rate_percent": 100.0}
        },
        "detailed_results": [
            {"id": "tc-001", "category": "happy_path", "query": "Test 1", "passed": True},
            {"id": "tc-002", "category": "happy_path", "query": "Test 2", "passed": False},
        ]
    }

    current_data = {
        "timestamp": "2026-09-04 10:05:00 UTC",
        "total_test_cases": 2,
        "configuration": {
            "reranker_enabled": True,
            "final_k": 3,
        },
        "metrics": {
            "keyword_baseline_accuracy_percent": 100.0,
            "retrieval_hit_rate_percent": 100.0,
            "retrieval_mrr_score": 0.95,
            "retrieval_latency_p50_ms": 3.5,
            "rbac_compliance_rate_percent": 100.0,
            "total_llm_calls_executed": 0,
        },
        "category_metrics": {
            "happy_path": {"total_cases": 2, "passed_cases": 2, "pass_rate_percent": 100.0}
        },
        "detailed_results": [
            {"id": "tc-001", "category": "happy_path", "query": "Test 1", "passed": True},
            {"id": "tc-002", "category": "happy_path", "query": "Test 2", "passed": True},
        ]
    }

    base_file = tmp_path / "base.json"
    base_file.write_text(json.dumps(baseline_data), encoding="utf-8")

    compare_and_print_reports(baseline_path=str(base_file), current_summary=current_data)
    captured = capsys.readouterr().out

    assert "EVALUATION A/B COMPARISON REPORT" in captured
    assert "RETRIEVAL CONFIGURATION COMPARISON" in captured
    assert "AGGREGATE METRICS DELTA" in captured
    assert "+10.00%" in captured
    assert "IMPROVEMENTS (Fail -> Pass)" in captured
    assert "tc-002" in captured
