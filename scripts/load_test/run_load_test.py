#!/usr/bin/env python3
"""
Enterprise IT Helpdesk Load Testing & CCU Scalability Runner
Simulates multi-tenant employee load with step-up stages:
10 -> 25 -> 50 -> 100 -> 200 CCU.
Measures p50, p95, p99 latency by Tier (L1/L2/L3) and error breakdown.
"""

import os
import sys
import time
import csv
import json
import random
import argparse
import concurrent.futures
from typing import Dict, List, Any
import urllib.request
import urllib.error

# Ensure root directory is on python path
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

EVAL_SET_PATH = os.path.join(CURRENT_DIR, "eval_set.csv")


def load_eval_queries() -> Dict[str, List[Dict[str, str]]]:
    """Loads evaluation dataset from CSV grouped by Tier."""
    data = {"L1": [], "L2": [], "L3": []}
    if not os.path.exists(EVAL_SET_PATH):
        print(f"Warning: {EVAL_SET_PATH} not found. Using defaults.")
        return {
            "L1": [{"query": "Cách đổi mật khẩu", "expected_system": "ALL"}],
            "L2": [{"query": "Tạo PO trên SAP", "expected_system": "ERP"}],
            "L3": [{"query": "Phân tích log lỗi sập DB", "expected_system": "ERP"}],
        }
    with open(EVAL_SET_PATH, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            tier = row.get("tier", "L1").strip()
            if tier in data:
                data[tier].append(row)
    return data


def make_request(base_url: str, endpoint: str, headers: dict) -> tuple[int, float, dict]:
    """Performs HTTP request and returns status code, latency in ms, and parsed JSON if available."""
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    req = urllib.request.Request(url, headers=headers)
    start = time.perf_counter()
    status_code = 0
    resp_data = {}
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            status_code = resp.status
            content = resp.read().decode("utf-8")
            try:
                resp_data = json.loads(content)
            except Exception:
                resp_data = {"raw": content}
    except urllib.error.HTTPError as e:
        status_code = e.code
    except Exception as e:
        status_code = 599  # Custom Client/Timeout Error
        resp_data = {"error": str(e)}

    latency_ms = (time.perf_counter() - start) * 1000.0
    return status_code, latency_ms, resp_data


def simulate_user_session(
    base_url: str,
    user_id: str,
    queries_by_tier: Dict[str, List[Dict[str, str]]],
    num_requests: int = 5
) -> List[dict]:
    """Simulates a single virtual user executing a mix of 60% L1, 30% L2, and 10% L3 queries."""
    results = []
    headers = {
        "Authorization": f"Bearer mock_dev_token_for_{user_id}",
        "Content-Type": "application/json",
        "X-User-ID": user_id,
        "X-User-Email": f"{user_id}@enterprise.com",
    }

    for _ in range(num_requests):
        # Weighted random selection: 60% L1, 30% L2, 10% L3
        r = random.random()
        if r < 0.60:
            tier = "L1"
        elif r < 0.90:
            tier = "L2"
        else:
            tier = "L3"

        tier_candidates = queries_by_tier.get(tier, [])
        query_item = random.choice(tier_candidates) if tier_candidates else {"query": "test query"}
        q_text = urllib.parse.quote(query_item["query"])

        # 1. Test Semantic Cache Lookup
        status, latency, data = make_request(
            base_url,
            f"api/cache/query?q={q_text}&threshold=0.92",
            headers
        )

        is_cache_hit = data.get("status") == "hit"
        results.append({
            "tier": tier,
            "status_code": status,
            "latency_ms": latency,
            "cache_hit": is_cache_hit,
            "user_id": user_id,
        })
        time.sleep(random.uniform(0.1, 0.5))

    return results


def calculate_percentiles(latencies: List[float]) -> dict:
    """Calculates p50, p95, p99 latencies."""
    if not latencies:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0}
    s = sorted(latencies)
    n = len(s)
    p50 = s[int(n * 0.50)]
    p95 = s[min(int(n * 0.95), n - 1)]
    p99 = s[min(int(n * 0.99), n - 1)]
    return {
        "p50": round(p50, 2),
        "p95": round(p95, 2),
        "p99": round(p99, 2),
    }


def run_stage(base_url: str, ccu: int, duration_secs: int = 10, queries_by_tier: dict = None) -> dict:
    """Executes a single CCU load stage and aggregates metrics."""
    print(f"\n🚀 Running Load Stage: {ccu} Concurrent Users (CCU) for {duration_secs}s...")
    start_stage = time.time()
    all_results = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=ccu) as executor:
        futures = []
        user_idx = 0
        while time.time() - start_stage < duration_secs:
            user_idx += 1
            u_id = f"user_ccu_{user_idx % ccu:03d}"
            futures.append(executor.submit(simulate_user_session, base_url, u_id, queries_by_tier, 3))
            time.sleep(0.05)

        for f in concurrent.futures.as_completed(futures):
            try:
                res = f.result()
                all_results.extend(res)
            except Exception as e:
                pass

    total_requests = len(all_results)
    if total_requests == 0:
        return {"ccu": ccu, "total_requests": 0, "error": "No requests recorded."}

    # Group latencies by tier
    latencies_by_tier = {"L1": [], "L2": [], "L3": []}
    error_counts = {"429_app": 0, "429_vertex": 0, "5xx": 0, "timeout": 0, "200_ok": 0}
    cache_hits = 0

    for r in all_results:
        t = r["tier"]
        latencies_by_tier[t].append(r["latency_ms"])
        if r["cache_hit"]:
            cache_hits += 1

        code = r["status_code"]
        if code == 200:
            error_counts["200_ok"] += 1
        elif code == 429:
            error_counts["429_app"] += 1
        elif code >= 500:
            error_counts["5xx"] += 1
        else:
            error_counts["timeout"] += 1

    p_l1 = calculate_percentiles(latencies_by_tier["L1"])
    p_l2 = calculate_percentiles(latencies_by_tier["L2"])
    p_l3 = calculate_percentiles(latencies_by_tier["L3"])
    all_latencies = [r["latency_ms"] for r in all_results]
    p_overall = calculate_percentiles(all_latencies)

    hit_rate = round((cache_hits / total_requests) * 100.0, 2)
    error_rate = round(((total_requests - error_counts["200_ok"]) / total_requests) * 100.0, 2)

    return {
        "ccu": ccu,
        "total_requests": total_requests,
        "throughput_rps": round(total_requests / duration_secs, 2),
        "hit_rate_pct": hit_rate,
        "error_rate_pct": error_rate,
        "error_breakdown": error_counts,
        "latency_overall": p_overall,
        "latency_l1": p_l1,
        "latency_l2": p_l2,
        "latency_l3": p_l3,
    }


def main():
    parser = argparse.ArgumentParser(description="IT Helpdesk Scalability & Load Testing Runner")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Target Server Base URL")
    parser.add_argument("--stages", default="10,25,50,100,200", help="Comma-separated CCU stages")
    parser.add_argument("--stage-duration", type=int, default=5, help="Duration in seconds per stage")
    parser.add_argument("--output", default="load_test_results.json", help="Output file for test results")
    args = parser.parse_args()

    queries = load_eval_queries()
    ccu_stages = [int(s.strip()) for s in args.stages.split(",") if s.strip()]

    print("=" * 70)
    print("🎯 IT HELPDESK AGENT: ENTERPRISE CCU SCALABILITY BENCHMARK")
    print(f"Target URL: {args.url}")
    print(f"CCU Stages: {ccu_stages}")
    print("=" * 70)

    stage_reports = []
    for ccu in ccu_stages:
        report = run_stage(args.url, ccu, duration_secs=args.stage_duration, queries_by_tier=queries)
        stage_reports.append(report)
        print(f"  -> CCU {ccu:03d} | RPS: {report.get('throughput_rps', 0):.1f} | Error: {report.get('error_rate_pct', 0)}% | Cache Hit: {report.get('hit_rate_pct', 0)}% | p95 Latency: {report.get('latency_overall', {}).get('p95', 0)}ms")

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(stage_reports, f, indent=2, ensure_ascii=False)
    print(f"\n✅ Load test completed. Results saved to {args.output}")


if __name__ == "__main__":
    main()
