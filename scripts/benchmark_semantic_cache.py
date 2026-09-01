"""
Benchmark script for Semantic Cache evaluating latency and throughput across 1,000 entries.
Acceptance verification for Backlog Round 3 (P2.5).
"""
import sys
import os
import time
import statistics
import fakeredis

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent_core.app_utils.semantic_cache import (
    InMemorySemanticCache,
    RedisSemanticCache,
)

def run_benchmark():
    print("=" * 70)
    print("  SEMANTIC CACHE BENCHMARK — 1,000 ENTRIES EVALUATION")
    print("=" * 70)

    num_entries = 1000
    queries = [f"Hướng dẫn xử lý sự cố mạng văn phòng và kết nối Wi-Fi phòng họp {i}" for i in range(num_entries)]
    responses = [f"Cách khắc phục sự cố Wi-Fi tại phòng {i}: Khởi động lại AP và kết nối SSID Corp-5G." for i in range(num_entries)]

    # -------------------------------------------------------------
    # 1. In-Memory Semantic Cache Benchmark
    # -------------------------------------------------------------
    mem_cache = InMemorySemanticCache(max_size=2000, similarity_threshold=0.85)
    print(f"\n[1] Populating InMemorySemanticCache with {num_entries} entries...")
    t0 = time.perf_counter()
    for q, r in zip(queries, responses):
        mem_cache.set(query=q, response=r, is_public=True)
    pop_time = time.perf_counter() - t0
    print(f"    -> Population completed in {pop_time:.3f}s ({num_entries / pop_time:.1f} writes/s)")

    # Read Hit Latency
    hit_latencies_ms = []
    for q in queries[:200]:
        t_start = time.perf_counter()
        res = mem_cache.get(q)
        dt = (time.perf_counter() - t_start) * 1000.0
        assert res is not None
        hit_latencies_ms.append(dt)

    # Read Miss Latency
    miss_latencies_ms = []
    for i in range(200):
        t_start = time.perf_counter()
        res = mem_cache.get(f"Câu hỏi hoàn toàn không liên quan gì đến IT hệ thống số {i*999}")
        dt = (time.perf_counter() - t_start) * 1000.0
        miss_latencies_ms.append(dt)

    mem_p50 = statistics.median(hit_latencies_ms)
    mem_p95 = statistics.quantiles(hit_latencies_ms, n=20)[18]
    mem_p99 = statistics.quantiles(hit_latencies_ms, n=100)[98]

    print("    -> In-Memory Results (1,000 entries pool):")
    print(f"       - Cache Hit Latency:  p50 = {mem_p50:.3f}ms | p95 = {mem_p95:.3f}ms | p99 = {mem_p99:.3f}ms")
    print(f"       - Cache Miss Latency: p50 = {statistics.median(miss_latencies_ms):.3f}ms")
    print(f"       - Read Throughput:    ~{1000.0 / mem_p50:.0f} queries/sec/core")

    # -------------------------------------------------------------
    # 2. Redis-backed Semantic Cache Benchmark (Multi-Tenant Candidate Set Vector Scan)
    # -------------------------------------------------------------
    fake_server = fakeredis.FakeServer()
    r = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
    redis_cache = RedisSemanticCache(redis_client=r, similarity_threshold=0.85)

    print(f"\n[2] Populating RedisSemanticCache with {num_entries} entries...")
    t0 = time.perf_counter()
    for q, r_resp in zip(queries, responses):
        redis_cache.set(query=q, response=r_resp, is_public=True)
    redis_pop_time = time.perf_counter() - t0
    print(f"    -> Population completed in {redis_pop_time:.3f}s ({num_entries / redis_pop_time:.1f} writes/s)")

    redis_hit_latencies_ms = []
    for q in queries[:200]:
        t_start = time.perf_counter()
        res = redis_cache.get(q)
        dt = (time.perf_counter() - t_start) * 1000.0
        assert res is not None
        redis_hit_latencies_ms.append(dt)

    redis_p50 = statistics.median(redis_hit_latencies_ms)
    redis_p95 = statistics.quantiles(redis_hit_latencies_ms, n=20)[18]
    redis_p99 = statistics.quantiles(redis_hit_latencies_ms, n=100)[98]

    print("    -> Redis Candidate-Set / Vector Search Results (1,000 entries pool):")
    print(f"       - Cache Hit Latency:  p50 = {redis_p50:.3f}ms | p95 = {redis_p95:.3f}ms | p99 = {redis_p99:.3f}ms")
    print(f"       - Speedup vs LLM:     ~{1200.0 / redis_p50:.0f}x faster than standard LLM invocation (1.2s -> {redis_p50:.2f}ms)")

    print("\n" + "=" * 70)
    print("  BENCHMARK SUMMARY COMPLETED SUCCESSFULLY")
    print("=" * 70)

if __name__ == "__main__":
    run_benchmark()
