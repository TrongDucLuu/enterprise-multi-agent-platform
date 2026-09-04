"""
Benchmark script for Semantic Cache evaluating latency, candidate count, and throughput
across configurable entry sizes (e.g. 1,000, 10,000, 50,000 entries).
Supports Backlog Round 7 Performance Criteria.
"""
import sys
import os
import time
import statistics
import argparse
import fakeredis

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


def run_benchmark(num_entries: int = 1000, num_lookups: int = 200):
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

    print(f"    -> In-Memory Results ({num_entries:,} entries pool):")
    print(f"       - Cache Hit Latency:  p50={mem_hit_stats['p50']}ms | p95={mem_hit_stats['p95']}ms | p99={mem_hit_stats['p99']}ms | min={mem_hit_stats['min']}ms | max={mem_hit_stats['max']}ms | avg={mem_hit_stats['avg']}ms")
    print(f"       - Cache Miss Latency: p50={mem_miss_stats['p50']}ms | p95={mem_miss_stats['p95']}ms | p99={mem_miss_stats['p99']}ms | avg={mem_miss_stats['avg']}ms")
    print(f"       - Read Throughput:    ~{1000.0 / max(mem_hit_stats['p50'], 0.001):,.0f} queries/sec/core")
    print(f"       - Candidate Limit:    {mem_cache.max_candidates} candidates max")

    # -------------------------------------------------------------
    # 2. Redis-backed Semantic Cache Benchmark (Multi-Tenant Candidate Set Vector Scan)
    # -------------------------------------------------------------
    fake_server = fakeredis.FakeServer()
    r = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
    redis_cache = RedisSemanticCache(redis_client=r, similarity_threshold=0.85)

    print(f"\n[2] Populating RedisSemanticCache with {num_entries:,} entries...")
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

    print(f"    -> Redis Candidate-Set / Vector Search Results ({num_entries:,} entries pool):")
    print(f"       - Cache Hit Latency:  p50={redis_hit_stats['p50']}ms | p95={redis_hit_stats['p95']}ms | p99={redis_hit_stats['p99']}ms | min={redis_hit_stats['min']}ms | max={redis_hit_stats['max']}ms | avg={redis_hit_stats['avg']}ms")
    print(f"       - Cache Miss Latency: p50={redis_miss_stats['p50']}ms | p95={redis_miss_stats['p95']}ms | p99={redis_miss_stats['p99']}ms | avg={redis_miss_stats['avg']}ms")
    print(f"       - Speedup vs LLM:     ~{1200.0 / max(redis_hit_stats['p50'], 0.001):,.0f}x faster than standard LLM invocation")

    # Verification threshold
    if num_entries >= 10000:
        assert mem_hit_stats["p95"] < 100.0, f"InMemory p95 {mem_hit_stats['p95']}ms exceeds 100ms SLA!"
        assert redis_hit_stats["p95"] < 100.0, f"Redis p95 {redis_hit_stats['p95']}ms exceeds 100ms SLA!"
        print(f"\n[PASS] SLA Verification: p95 latency ({mem_hit_stats['p95']}ms / {redis_hit_stats['p95']}ms) is strictly < 100ms at {num_entries:,} entries.")

    print("\n" + "=" * 80)
    print("  BENCHMARK SUMMARY COMPLETED SUCCESSFULLY")
    print("=" * 80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark Semantic Cache scale and latency.")
    parser.add_argument("--entries", type=int, default=1000, help="Number of entries in cache pool (e.g. 1000, 10000, 50000)")
    parser.add_argument("--lookups", type=int, default=200, help="Number of lookups to evaluate")
    args = parser.parse_args()

    run_benchmark(num_entries=args.entries, num_lookups=args.lookups)

