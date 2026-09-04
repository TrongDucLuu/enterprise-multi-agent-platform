"""
Benchmark script for Semantic Cache evaluating latency, candidate count, throughput,
and hit rate across configurable entry sizes (e.g. 1,000, 10,000, 50,000 entries).
Supports Backlog Round 7 & Round 8 Performance Criteria.
"""
import sys
import os
import time
import math
import statistics
import argparse
from typing import Optional

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent_core.app_utils.semantic_cache import (
    InMemorySemanticCache,
    RedisSemanticCache,
    GLOBAL_CACHE_METRICS,
)


def calc_stats(latencies: list[float]) -> dict:
    if not latencies:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "avg": 0.0}
    n = len(latencies)
    sorted_l = sorted(latencies)
    p50 = statistics.median(latencies)
    p95 = sorted_l[int(0.95 * n)] if n >= 20 else sorted_l[-1]
    p99 = sorted_l[int(0.99 * n)] if n >= 100 else sorted_l[-1]
    return {
        "p50": round(p50, 3),
        "p95": round(p95, 3),
        "p99": round(p99, 3),
        "min": round(min(latencies), 3),
        "max": round(max(latencies), 3),
        "avg": round(statistics.mean(latencies), 3),
    }


def compute_hit_rate(
    mem_cache: InMemorySemanticCache,
    test_queries: list[str],
    similarity_threshold: float = 0.85,
) -> tuple[int, int, float]:
    """
    Measures the hit rate of candidate-set retrieval (max 200 candidates)
    compared to an exhaustive full scan across all entries.
    """
    candidate_hits = 0
    full_scan_hits = 0

    for q in test_queries:
        # 1. Candidate-limited get (Standard cache path)
        res_cand = mem_cache.get(q)
        if res_cand is not None:
            candidate_hits += 1

        # 2. Exhaustive full-scan across all entries
        q_emb = mem_cache._generate_embedding(q)
        full_hit = False
        if q_emb:
            for entry in mem_cache._entries:
                if entry.embedding:
                    sim = sum(a * b for a, b in zip(q_emb, entry.embedding))
                    if sim >= similarity_threshold:
                        full_hit = True
                        break
        if full_hit:
            full_scan_hits += 1

    hit_rate = (candidate_hits / full_scan_hits * 100.0) if full_scan_hits > 0 else 100.0
    return candidate_hits, full_scan_hits, hit_rate


def run_benchmark(
    num_entries: int = 1000,
    num_lookups: int = 200,
    redis_host: Optional[str] = None,
    redis_port: int = 6379,
    llm_baseline_ms: float = 1200.0,
):
    print("=" * 80)
    print(f"  SEMANTIC CACHE BENCHMARK — {num_entries:,} ENTRIES | {num_lookups} LOOKUPS")
    print("=" * 80)

    queries = [f"Hướng dẫn xử lý sự cố mạng văn phòng và kết nối Wi-Fi phòng họp {i}" for i in range(num_entries)]
    responses = [f"Cách khắc phục sự cố Wi-Fi tại phòng {i}: Khởi động lại AP và kết nối SSID Corp-5G." for i in range(num_entries)]

    # -------------------------------------------------------------
    # 1. In-Memory Semantic Cache Benchmark
    # -------------------------------------------------------------
    mem_cache = InMemorySemanticCache(max_size=max(num_entries + 1000, 2000), similarity_threshold=0.85)
    print(f"\n[1] Populating InMemorySemanticCache with {num_entries:,} entries...")
    t0 = time.perf_counter()
    for q, r in zip(queries, responses):
        mem_cache.set(query=q, response=r, is_public=True)
    pop_time = time.perf_counter() - t0
    print(f"    -> Population completed in {pop_time:.3f}s ({num_entries / pop_time:,.1f} writes/s)")

    # Read Hit Latency (Exact & Near)
    hit_latencies_ms = []
    for q in queries[:num_lookups]:
        t_start = time.perf_counter()
        res = mem_cache.get(q)
        dt = (time.perf_counter() - t_start) * 1000.0
        assert res is not None, f"Expected cache hit for query: {q}"
        hit_latencies_ms.append(dt)

    # Read Miss Latency
    miss_latencies_ms = []
    for i in range(num_lookups):
        t_start = time.perf_counter()
        res = mem_cache.get(f"Câu hỏi hoàn toàn không liên quan gì đến IT hệ thống số {i * 99999 + 7}")
        dt = (time.perf_counter() - t_start) * 1000.0
        miss_latencies_ms.append(dt)

    mem_hit_stats = calc_stats(hit_latencies_ms)
    mem_miss_stats = calc_stats(miss_latencies_ms)

    # Hit rate measurement with mixed test queries
    eval_queries = queries[:min(100, num_entries)] + [
        f"Làm sao kết nối Wi-Fi ở phòng họp {i} khi mạng chập chờn?" for i in range(min(50, num_entries))
    ]
    cand_hits, full_hits, hit_rate = compute_hit_rate(mem_cache, eval_queries, similarity_threshold=0.85)

    print(f"    -> In-Memory Results ({num_entries:,} entries pool):")
    print(f"       - Cache Hit Latency:  p50={mem_hit_stats['p50']}ms | p95={mem_hit_stats['p95']}ms | p99={mem_hit_stats['p99']}ms | min={mem_hit_stats['min']}ms | max={mem_hit_stats['max']}ms | avg={mem_hit_stats['avg']}ms")
    print(f"       - Cache Miss Latency: p50={mem_miss_stats['p50']}ms | p95={mem_miss_stats['p95']}ms | p99={mem_miss_stats['p99']}ms | avg={mem_miss_stats['avg']}ms")
    print(f"       - Read Throughput:    ~{1000.0 / max(mem_hit_stats['p50'], 0.001):,.0f} queries/sec/core")
    print(f"       - Candidate Limit:    {mem_cache.max_candidates} candidates max")
    print(f"       - Hit Rate vs Full:   {cand_hits}/{full_hits} ({hit_rate:.1f}%) [Candidate Limit: {mem_cache.max_candidates} vs Pool: {num_entries:,}]")

    # -------------------------------------------------------------
    # 2. Redis-backed Semantic Cache Benchmark (Multi-Tenant Candidate Set Vector Scan)
    # -------------------------------------------------------------
    is_live_redis = bool(redis_host)
    if is_live_redis:
        try:
            import redis
            r = redis.Redis(host=redis_host, port=redis_port, decode_responses=True)
            r.ping()
            redis_mode_label = f"LIVE NETWORK REDIS ({redis_host}:{redis_port})"
        except Exception as e:
            print(f"    [WARN] Failed to connect to live Redis at {redis_host}:{redis_port}: {e}")
            print("    Falling back to fakeredis simulation.")
            import fakeredis
            fake_server = fakeredis.FakeServer()
            r = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
            redis_mode_label = "IN-PROCESS SIMULATION — network cost not measured"
    else:
        import fakeredis
        fake_server = fakeredis.FakeServer()
        r = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
        redis_mode_label = "IN-PROCESS SIMULATION — network cost not measured"

    redis_cache = RedisSemanticCache(redis_client=r, similarity_threshold=0.85)

    print(f"\n[2] Populating RedisSemanticCache with {num_entries:,} entries [{redis_mode_label}]...")
    t0 = time.perf_counter()
    for q, r_resp in zip(queries, responses):
        redis_cache.set(query=q, response=r_resp, is_public=True)
    redis_pop_time = time.perf_counter() - t0
    print(f"    -> Population completed in {redis_pop_time:.3f}s ({num_entries / redis_pop_time:,.1f} writes/s)")

    redis_hit_latencies_ms = []
    for q in queries[:num_lookups]:
        t_start = time.perf_counter()
        res = redis_cache.get(q)
        dt = (time.perf_counter() - t_start) * 1000.0
        assert res is not None, f"Expected Redis cache hit for query: {q}"
        redis_hit_latencies_ms.append(dt)

    redis_miss_latencies_ms = []
    for i in range(num_lookups):
        t_start = time.perf_counter()
        res = redis_cache.get(f"Câu hỏi hoàn toàn không liên quan gì đến IT hệ thống số {i * 99999 + 7}")
        dt = (time.perf_counter() - t_start) * 1000.0
        redis_miss_latencies_ms.append(dt)

    redis_hit_stats = calc_stats(redis_hit_latencies_ms)
    redis_miss_stats = calc_stats(redis_miss_latencies_ms)
    speedup = llm_baseline_ms / max(redis_hit_stats['p50'], 0.001)

    print(f"    -> Redis Candidate-Set / Vector Search Results ({num_entries:,} entries pool) [{redis_mode_label}]:")
    print(f"       - Cache Hit Latency:  p50={redis_hit_stats['p50']}ms | p95={redis_hit_stats['p95']}ms | p99={redis_hit_stats['p99']}ms | min={redis_hit_stats['min']}ms | max={redis_hit_stats['max']}ms | avg={redis_hit_stats['avg']}ms")
    print(f"       - Cache Miss Latency: p50={redis_miss_stats['p50']}ms | p95={redis_miss_stats['p95']}ms | p99={redis_miss_stats['p99']}ms | avg={redis_miss_stats['avg']}ms")
    print(f"       - Speedup vs LLM:     ~{speedup:,.0f}x faster than standard LLM invocation (assumed {llm_baseline_ms:.0f}ms baseline)")

    # Verification threshold
    if num_entries >= 10000:
        assert mem_hit_stats["p95"] < 100.0, f"InMemory p95 {mem_hit_stats['p95']}ms exceeds 100ms SLA!"
        assert redis_hit_stats["p95"] < 100.0, f"Redis p95 {redis_hit_stats['p95']}ms exceeds 100ms SLA!"
        print(f"\n[PASS] SLA Verification: p95 latency ({mem_hit_stats['p95']}ms / {redis_hit_stats['p95']}ms) is strictly < 100ms at {num_entries:,} entries.")

    print("\n" + "=" * 80)
    print("  BENCHMARK SUMMARY COMPLETED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark Semantic Cache scale, latency, and hit rate.")
    parser.add_argument("--entries", type=int, default=1000, help="Number of entries in cache pool (e.g. 1000, 10000, 50000)")
    parser.add_argument("--lookups", type=int, default=200, help="Number of lookups to evaluate")
    parser.add_argument("--redis-host", type=str, default=None, help="Optional live Redis host for real network round-trip measurement")
    parser.add_argument("--redis-port", type=int, default=6379, help="Optional live Redis port (default: 6379)")
    parser.add_argument("--llm-baseline-ms", type=float, default=1200.0, help="Assumed baseline latency of direct LLM invocation in ms (default: 1200.0)")
    args = parser.parse_args()

    run_benchmark(
        num_entries=args.entries,
        num_lookups=args.lookups,
        redis_host=args.redis_host,
        redis_port=args.redis_port,
        llm_baseline_ms=args.llm_baseline_ms,
    )

