# Enterprise RAG Quality & Retrieval Baselines

Thư mục này lưu trữ các baseline chính thức và bảng theo dõi delta chất lượng truy xuất (RAG Quality & Retrieval Benchmarks) theo từng PR.

---

## 📌 1. Baseline Chính thức (Phase 0 — PR 1)

- **Ngày ghi nhận**: 2026-09-04
- **Commit Base**: `a09c93a`
- **Domain Pack**: `it-helpdesk` (52 knowledge chunks)
- **Tập Eval**: `domain_packs/it-helpdesk/eval_set.jsonl` (127 test cases, 72 cases đo IR Retrieval)
- **File Baseline**: [`baselines/baseline_offline.json`](./baseline_offline.json)

### Cấu hình Retrieval Mặc định
| Tham số | Giá trị |
| :--- | :--- |
| `retrieve_k` | 20 |
| `final_k` | 3 |
| `adaptive_retrieval_rounds` | 2 |
| `hybrid_search_enabled` | True |
| `reranker_enabled` | False |
| `query_preprocessing_enabled` | False |
| `query_rewrite_enabled` | False |
| `corrective_retrieval_enabled` | False |
| `fraction_lists_to_search` | 0.05 |
| `embedding_model` | `text-embedding-005` |

### Chỉ số Baseline (Phase 0)
| Chỉ số (Metric) | Baseline | Mục tiêu Quality Gate | Trạng thái |
| :--- | :---: | :---: | :---: |
| **Keyword Baseline Accuracy** | **94.49%** (120/127) | $\ge 85.0\%$ | ✅ PASS |
| **L2 RAG Groundedness Rate** | **100.0%** (36/36) | $\ge 80.0\%$ | ✅ PASS |
| **Retrieval Hit Rate@k** | **95.83%** (69/72) | $\ge 80.0\%$ | ✅ PASS |
| **Retrieval Precision@k (Avg)** | **0.324** / 1.0 | N/A | ℹ️ INFO |
| **Retrieval Recall@k (Avg)** | **0.958** / 1.0 | N/A | ℹ️ INFO |
| **Retrieval MRR Score** | **0.914** / 1.0 | $\ge 0.80$ | ✅ PASS |
| **Trap Question Refusal Rate** | **91.43%** (32/35) | $\ge 90.0\%$ | ✅ PASS |
| **Indirect Injection Defense** | **100.0%** (2/2) | $\ge 100.0\%$ | ✅ PASS |
| **RBAC Persona Compliance** | **100.0%** (15/15) | $\ge 100.0\%$ | ✅ PASS |
| **Retrieval Latency (p50 / p95)** | **2.08 ms / 2.50 ms** | N/A | ℹ️ FAST |

---

## 📊 2. Bảng Theo dõi Delta Lũy kế theo từng PR

> **Nguyên tắc**: Không tối ưu nào được merge mà không có delta đo được. Mọi tối ưu đều được bật bằng cờ trong khối `retrieval` của pack và so sánh trên cùng bộ eval.

| PR # | Hạng mục Tối ưu | Hit Rate | MRR | Latency p50 | Latency p95 | RBAC Pass | Trạng thái | Ghi chú |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **PR 1** | **Baseline (Phase 0: P0-A & P0-B)** | 95.83% | 0.914 | 2.08 ms | 2.50 ms | 100.0% | **BASELINE** | Thiết lập so sánh A/B & mở rộng eval set 127 cases |
| **PR 2** | **Hạng mục A: Tiền xử lý & Query Rewrite** | - | - | - | - | - | *Pending* | A1 (không LLM) + A2 (Rewrite LLM) |
| **PR 3** | **Hạng mục B: Vertex AI Semantic Reranker** | - | - | - | - | - | *Pending* | Cờ `reranker_enabled: true` |
| **PR 4** | **Hạng mục C: Corrective Retrieval Loop** | - | - | - | - | - | *Pending* | Cờ `corrective_retrieval_enabled: true` |
| **PR 5** | **Hạng mục D: Thử nghiệm Chunking** | - | - | - | - | - | *Pending* | So sánh kích thước chunk & overlap |

---

## 🛠️ 3. Hướng dẫn Chạy So sánh A/B

```bash
# 1. Chạy xuất baseline mới
python scripts/eval_harness.py --offline --output baselines/baseline_offline.json

# 2. Chạy so sánh run hiện tại với baseline đã lưu
python scripts/eval_harness.py --offline --compare baselines/baseline_offline.json

# 3. Chạy với Top-K tùy chỉnh và seed cố định
python scripts/eval_harness.py --offline -k 5 --seed 42 --compare baselines/baseline_offline.json
```
