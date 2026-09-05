#!/usr/bin/env python3
"""
Chunking Strategy Benchmark Suite (Phase 1 Item D [R2]).

Benchmarks chunking strategies across token sizes (200, 400, 800 tokens), overlap ratios (10%, 15%, 20%),
and splitters (markdown-aware section splitting vs recursive character splitting).
Measures chunk count, chunk density, code block integrity, and retrieval metrics (Hit Rate@k, MRR, Latency).

Usage:
    python scripts/benchmark_chunking.py
    python scripts/benchmark_chunking.py --domain-pack it-helpdesk --json
"""

import os
import sys
import json
import time
import argparse
from pathlib import Path
from typing import Any, Dict, List

# Ensure repository root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import yaml
from agent_core.tools.enterprise_rag_mcp.rag_models import KnowledgeArticle, SecurityContext
from agent_core.tools.enterprise_rag_mcp.knowledge.in_memory import InMemoryKnowledgeStore
from scripts.ingest.chunkers import (
    estimate_tokens,
    chunk_by_sections,
    chunk_text,
    benchmark_chunking_configurations,
)
from scripts.eval_harness import load_eval_dataset


def load_raw_documents(domain_pack: str = "it-helpdesk") -> List[Dict[str, Any]]:
    """Loads sample raw articles and documents for chunking benchmarking."""
    docs: List[Dict[str, Any]] = []
    sample_dir = PROJECT_ROOT / "domain_packs" / domain_pack / "sample_data"
    
    for filename in ("articles.yaml", "technical_manuals.yaml"):
        file_path = sample_dir / filename
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if data and isinstance(data, list):
                        docs.extend(data)
            except Exception as e:
                pass
    return docs


def run_chunking_benchmark(
    domain_pack: str = "it-helpdesk",
    k: int = 3,
) -> Dict[str, Any]:
    """Executes chunking experiments across multiple configurations and measures retrieval deltas."""
    raw_docs = load_raw_documents(domain_pack)
    eval_cases = load_eval_dataset(limit=None)
    retrieval_cases = [c for c in eval_cases if c.get("tier") == "L2" and not c.get("is_unanswerable")]

    configs = [
        {"name": "Markdown-Aware 200T (10% ovlp)", "strategy": "markdown_aware", "max_tokens": 200, "overlap_ratio": 0.10},
        {"name": "Markdown-Aware 400T (15% ovlp)", "strategy": "markdown_aware", "max_tokens": 400, "overlap_ratio": 0.15},
        {"name": "Markdown-Aware 800T (20% ovlp)", "strategy": "markdown_aware", "max_tokens": 800, "overlap_ratio": 0.20},
        {"name": "Recursive Char 200T (10% ovlp)", "strategy": "recursive", "max_tokens": 200, "overlap_ratio": 0.10},
        {"name": "Recursive Char 400T (15% ovlp)", "strategy": "recursive", "max_tokens": 400, "overlap_ratio": 0.15},
        {"name": "Recursive Char 800T (20% ovlp)", "strategy": "recursive", "max_tokens": 800, "overlap_ratio": 0.20},
    ]

    base_results = benchmark_chunking_configurations(raw_docs, configs)
    benchmark_report = []

    admin_sec_ctx = SecurityContext.from_user(user_id="benchmark-eval", roles=["admin", "it_admin"], clearance_level=3)

    for idx, cfg_res in enumerate(base_results):
        cfg = configs[idx]
        strategy = cfg["strategy"]
        max_tokens = cfg["max_tokens"]
        overlap_ratio = cfg["overlap_ratio"]
        max_chars = max_tokens * 4
        overlap_chars = int(max_chars * overlap_ratio)

        # Build chunked KnowledgeArticle items
        chunked_articles: List[KnowledgeArticle] = []
        for d in raw_docs:
            content = d.get("content", "")
            title = d.get("title", "")
            sys_name = d.get("system", "ERP")
            doc_id = d.get("id", "KB-001")
            roles = d.get("allowed_roles", ["*"])
            clearance = d.get("clearance_level", 1)
            keywords = d.get("keywords", [])

            sections = d.get("sections", [])
            content = d.get("content", "")
            if not content and sections:
                content = "\n\n".join(f"## {s.get('heading', '')}\n{s.get('content', '')}" for s in sections)

            if strategy == "markdown_aware":
                doc_sections = sections if sections else [{"heading": title, "content": content}]
                chunks = chunk_by_sections(doc_sections, max_chunk_size=max_chars, overlap=overlap_chars)
            else:
                chunks = chunk_text(content, max_chunk_size=max_chars, overlap=overlap_chars)

            for c_idx, c_text in enumerate(chunks):
                chunk_art_id = f"{doc_id}-{c_idx}" if len(chunks) > 1 else doc_id
                chunked_articles.append(
                    KnowledgeArticle(
                        id=chunk_art_id,
                        parent_doc_id=doc_id,
                        chunk_index=c_idx,
                        system=sys_name,
                        title=f"{title} (Part {c_idx+1})" if len(chunks) > 1 else title,
                        content=c_text,
                        category=d.get("category", "General"),
                        keywords=keywords,
                        allowed_roles=roles,
                        clearance_level=clearance,
                    )
                )

        # Measure retrieval metrics against evaluation dataset
        store = InMemoryKnowledgeStore(articles=chunked_articles)
        hits = 0
        precisions = []
        recalls = []
        rr_scores = []
        latencies = []

        for tc in retrieval_cases:
            q = tc["query"]
            expected_ids = tc.get("expected_source_ids", []) or tc.get("expected_citations", [])
            if not expected_ids:
                continue

            system = tc.get("expected_system") if tc.get("expected_system") not in ("ALL", "NONE") else None
            t0 = time.perf_counter()
            results = store.search(query=q, security_context=admin_sec_ctx, system=system, limit=k)
            t_elapsed_ms = (time.perf_counter() - t0) * 1000.0
            latencies.append(t_elapsed_ms)

            retrieved_ids = [getattr(r, "article_id", "") for r in results]
            
            # Match if retrieved ID equals expected ID or starts with expected parent doc ID
            matched_ids = []
            for exp_id in expected_ids:
                for rid in retrieved_ids:
                    if rid == exp_id or rid.startswith(f"{exp_id}-") or exp_id.startswith(f"{rid}-"):
                        matched_ids.append(rid)
                        break

            hit = len(matched_ids) > 0
            if hit:
                hits += 1

            first_rank = None
            for r_idx, rid in enumerate(retrieved_ids, start=1):
                if any(rid == exp_id or rid.startswith(f"{exp_id}-") or exp_id.startswith(f"{rid}-") for exp_id in expected_ids):
                    first_rank = r_idx
                    break

            mrr = (1.0 / first_rank) if first_rank else 0.0
            p_at_k = (len(matched_ids) / len(retrieved_ids)) if retrieved_ids else 0.0
            r_at_k = (len(matched_ids) / len(expected_ids)) if expected_ids else 0.0

            precisions.append(p_at_k)
            recalls.append(r_at_k)
            rr_scores.append(mrr)

        total_ret_cases = max(1, len(retrieval_cases))
        hit_rate = round((hits / total_ret_cases) * 100, 2)
        avg_precision = round(sum(precisions) / total_ret_cases, 3)
        avg_recall = round(sum(recalls) / total_ret_cases, 3)
        avg_mrr = round(sum(rr_scores) / total_ret_cases, 3)
        p50_lat = round(sorted(latencies)[len(latencies) // 2], 2) if latencies else 0.0

        benchmark_report.append({
            **cfg_res,
            "hit_rate_at_k": hit_rate,
            "precision_at_k": avg_precision,
            "recall_at_k": avg_recall,
            "mrr": avg_mrr,
            "latency_p50_ms": p50_lat,
        })

    return {
        "domain_pack": domain_pack,
        "k": k,
        "total_documents": len(raw_docs),
        "total_retrieval_cases": len(retrieval_cases),
        "benchmarks": benchmark_report,
    }


def print_chunking_report(summary: Dict[str, Any]):
    """Prints a beautiful markdown comparison table of chunking benchmark results."""
    print("\n" + "=" * 90)
    print("📊 CHUNKING STRATEGY & SIZE BENCHMARK REPORT (PHASE 1 ITEM D [R2])")
    print("=" * 90)
    print(f"• Domain Pack: {summary.get('domain_pack')}")
    print(f"• Base Documents: {summary.get('total_documents')}")
    print(f"• Evaluated Retrieval Test Cases: {summary.get('total_retrieval_cases')}")
    print(f"• Top-K Cutoff: {summary.get('k')}\n")

    headers = ["Configuration", "Strategy", "Tokens", "Chunks", "Avg Len (chars)", "Code Int (%)", "Hit Rate@k", "MRR", "p50 (ms)"]
    rows = []
    for b in summary.get("benchmarks", []):
        rows.append([
            b["config_name"],
            b["strategy"],
            f"{b['max_tokens']}T",
            str(b["total_chunks"]),
            str(b["avg_chars"]),
            f"{b['code_block_integrity_pct']}%",
            f"{b['hit_rate_at_k']}%",
            f"{b['mrr']:.3f}",
            f"{b['latency_p50_ms']} ms",
        ])

    col_widths = [max(len(r[i]) for r in ([headers] + rows)) + 2 for i in range(len(headers))]
    header_str = "".join(headers[i].ljust(col_widths[i]) for i in range(len(headers)))
    sep_str = "".join("-" * (col_widths[i] - 1) + " " for i in range(len(headers)))

    print(header_str)
    print(sep_str)
    for r in rows:
        print("".join(r[i].ljust(col_widths[i]) for i in range(len(r))))
    print("-" * 90 + "\n")


def main():
    parser = argparse.ArgumentParser(description="Benchmark chunking strategies and token sizes")
    parser.add_argument("--domain-pack", type=str, default="it-helpdesk", help="Domain pack name")
    parser.add_argument("-k", "--k", type=int, default=3, help="Top-K cutoff for retrieval")
    parser.add_argument("--json", action="store_true", help="Output JSON results")
    args = parser.parse_args()

    summary = run_chunking_benchmark(domain_pack=args.domain_pack, k=args.k)
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        print_chunking_report(summary)


if __name__ == "__main__":
    main()
