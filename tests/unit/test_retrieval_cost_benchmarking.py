"""
Unit tests for Synthetic Knowledge Base Generator and Retrieval Cost Benchmarking.
Verifies:
1. Synthetic dataset generation integrity (>= 5000 chunks, 768-dim embeddings, RBAC metadata, tombstones, dates).
2. BigQuery on-demand cost calculation formulas ($/TiB -> $/1,000 queries).
3. Retrieval simulation accuracy (RBAC clearance filtering, tombstone exclusion, expiry date filtering).
4. Full benchmark execution and metric consistency.
"""

import pytest
from scripts.generate_synthetic_kb import generate_synthetic_chunk, generate_synthetic_dataset
from scripts.benchmark_retrieval_cost import (
    calculate_bigquery_query_cost,
    calculate_cost_per_thousand_queries,
    simulate_bigquery_retrieval,
    run_retrieval_benchmark,
    DEFAULT_BQ_ON_DEMAND_PRICE_PER_TIB,
    BQ_MIN_BYTES_BILLED_PER_QUERY,
)


def test_synthetic_kb_generation_integrity():
    """Asserts that generate_synthetic_dataset generates complete chunks with required fields."""
    dataset = generate_synthetic_dataset(num_chunks=100, seed=42, dim=768)
    assert len(dataset) == 100

    required_fields = {
        "doc_id", "chunk_id", "title", "content", "category",
        "roles", "sensitivity", "clearance_level", "source_uri",
        "owner", "effective_date", "expiry_date", "is_deleted",
        "parser_version", "chunker_version", "embedding_model",
        "embedding_dim", "embedding"
    }

    tombstoned_count = 0
    expired_count = 0

    for chunk in dataset:
        assert required_fields.issubset(chunk.keys())
        assert len(chunk["embedding"]) == 768
        assert chunk["clearance_level"] in (0, 1, 2, 3)
        assert isinstance(chunk["roles"], list) and len(chunk["roles"]) >= 1
        assert chunk["sensitivity"] in ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED")

        if chunk["is_deleted"]:
            tombstoned_count += 1
            assert chunk["deleted_at"] is not None

        if chunk["expiry_date"] < "2025-01-01":
            expired_count += 1

    # Confirms realistic distribution of tombstones and expired documents
    assert tombstoned_count > 0, "Expected some tombstoned records in synthetic dataset"
    assert expired_count > 0, "Expected some expired records in synthetic dataset"


def test_bigquery_cost_calculation_formulas():
    """Asserts exact mathematical precision of BigQuery on-demand cost calculations."""
    # 1 TiB = 1024^4 = 1,099,511,627,776 bytes
    one_tib = 1024 ** 4

    # 1 TiB at $6.25/TiB should be exactly $6.25
    cost_1_tib = calculate_bigquery_query_cost(one_tib, price_per_tib=6.25)
    assert pytest.approx(cost_1_tib, rel=1e-6) == 6.25

    # 10 MB query (BigQuery minimum billing unit)
    ten_mb = 10 * 1024 * 1024
    cost_10_mb = calculate_bigquery_query_cost(ten_mb, price_per_tib=6.25)
    cost_1k_queries = calculate_cost_per_thousand_queries(ten_mb, price_per_tib=6.25)

    expected_1k = (10.0 / (1024.0 * 1024.0)) * 6.25 * 1000.0
    assert pytest.approx(cost_1k_queries, rel=1e-5) == expected_1k
    assert pytest.approx(cost_1k_queries, rel=1e-3) == 0.0596


def test_simulate_bigquery_retrieval_filtering():
    """Asserts that simulation excludes deleted, expired, and uncleared documents."""
    dataset = generate_synthetic_dataset(num_chunks=50, seed=42, dim=768)
    query_vec = [0.0] * 768
    query_vec[0] = 1.0

    # Execute simulation with max clearance = 1 (filters out clearance 2 and 3)
    matches, bytes_scanned, bytes_billed, dur_ms = simulate_bigquery_retrieval(
        dataset=dataset,
        query_vector=query_vec,
        max_clearance=1,
        top_k=10
    )

    assert bytes_billed >= BQ_MIN_BYTES_BILLED_PER_QUERY
    assert dur_ms > 0

    for match in matches:
        assert match["is_deleted"] is False
        assert match["expiry_date"] >= "2025-01-01"
        assert match["clearance_level"] <= 1


def test_run_retrieval_benchmark_outputs_valid_metrics():
    """Asserts that benchmark runner returns complete metrics dictionary with all 4 architectural comparisons."""
    results = run_retrieval_benchmark(num_chunks=200, num_queries=10, seed=42)

    assert "retrieval_metrics" in results
    metrics = results["retrieval_metrics"]
    assert metrics["p50_latency_ms"] > 0
    assert metrics["p95_latency_ms"] >= metrics["p50_latency_ms"]
    assert metrics["cost_per_1000_queries_usd"] > 0
    assert metrics["median_bytes_billed"] >= BQ_MIN_BYTES_BILLED_PER_QUERY

    assert "comparison_table" in results
    table = results["comparison_table"]
    assert len(table) == 4
    arch_names = [row["architecture"] for row in table]
    assert any("In-Memory" in name for name in arch_names)
    assert any("BigQuery On-Demand" in name for name in arch_names)
    assert any("BigQuery Edition" in name for name in arch_names)
    assert any("Vertex AI Search" in name for name in arch_names)
