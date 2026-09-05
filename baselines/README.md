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
| **PR 2** | **Hạng mục A: Tiền xử lý & Query Rewrite** | 95.83% (0.00%) | 0.914 (0.000) | 2.07 ms (-0.01 ms) | 2.58 ms (+0.08 ms) | 100.0% (0.00%) | ✅ MERGED | A1 (Unicode/stop-words/code-safe) + A2 (Rewrite LLM fallback) |
| **PR 3** | **Hạng mục B: Vertex AI Semantic Reranker** | 95.83% (0.00%) | 0.884 (-0.030) | 2.63 ms (+0.55 ms) | 3.51 ms (+1.01 ms) | 100.0% (0.00%) | ✅ MERGED | Semantic ranker Discovery Engine + offline cross-field ranking |
| **PR 4** | **Hạng mục C: Corrective Retrieval Loop** | 95.83% (0.00%) | 0.914 (0.000) | 2.02 ms (-0.06 ms) | 2.53 ms (+0.03 ms) | 100.0% (0.00%) | ✅ MERGED | Cờ `corrective_retrieval_enabled: true`, adaptive rounds & confidence threshold |
| **PR 5** | **Hạng mục D: Thử nghiệm Chunking** | 95.83% (0.00%) | 0.914 (0.000) | 1.89 ms (-0.19 ms) | 2.45 ms (-0.05 ms) | 100.0% (0.00%) | ✅ MERGED | Markdown heading-aware vs recursive character splitters, token sizes 200/400/800T |

---

## 🧩 3. Kết Quả Thử Nghiệm Chunking (Phase 1 Item D [R2])

Thử nghiệm đa cấu hình chunking trên tập tài liệu kỹ thuật IT runbooks, cẩm nang xử lý lỗi và cẩm nang cấu hình hệ thống:

| Cấu hình | Chiến lược (Strategy) | Kích thước Chunk | Overlap | Số lượng Chunks | Độ dài TB (Ký tự) | Bảo toàn Code Block | Hit Rate@3 | MRR Score | Latency p50 |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Markdown-Aware 200T** | `markdown_aware` | 200 Tokens (~800 chars) | 10% | 58 | 323.3 | **100.0%** | **88.24%** | **0.819** | 1.93 ms |
| **Markdown-Aware 400T** | `markdown_aware` | 400 Tokens (~1600 chars) | 15% | 58 | 323.3 | **100.0%** | **88.24%** | **0.819** | 1.89 ms |
| **Markdown-Aware 800T** | `markdown_aware` | 800 Tokens (~3200 chars) | 20% | 58 | 323.3 | **100.0%** | **88.24%** | **0.819** | 1.89 ms |
| **Recursive Char 200T** | `recursive` | 200 Tokens (~800 chars) | 10% | 57 | 328.9 | **100.0%** | **88.24%** | **0.819** | 1.84 ms |
| **Recursive Char 400T** | `recursive` | 400 Tokens (~1600 chars) | 15% | 54 | 347.4 | **100.0%** | **88.24%** | **0.819** | 1.81 ms |
| **Recursive Char 800T** | `recursive` | 800 Tokens (~3200 chars) | 20% | 54 | 347.4 | **100.0%** | **88.24%** | **0.819** | 1.81 ms |

### 📌 Đánh giá và Khuyến nghị
1. **Markdown-Aware Splitting (`chunk_by_sections`)**:
   - Bảo toàn phân cấp tiêu đề (`#`, `##`, `###`), gắn kèm ngữ cảnh cha-con (breadcrumbs) vào từng chunk.
   - Giữ nguyên vẹn 100% các khối mã lệnh shell (` ```bash `) và bảng thông số cấu hình markdown (`| Col | ... |`), tránh việc cắt ngang dòng lệnh.
2. **Cấu hình Tối ưu Khuyến nghị**:
   - Đối với tài liệu kỹ thuật / Runbooks có cấu trúc tiêu đề rõ ràng: Áp dụng `strategy: "auto"` / `strategy: "markdown_aware"` với `max_chunk_size: 1200-1600 chars` (~300-400 tokens) và `overlap: 150-200 chars` (15%).

---

## 🛠️ 4. Hướng dẫn Chạy So sánh A/B & Benchmark Chunking

```bash
# 1. Chạy xuất baseline mới
python scripts/eval_harness.py --offline --output baselines/baseline_offline.json

# 2. Chạy so sánh run hiện tại với baseline đã lưu
python scripts/eval_harness.py --offline --compare baselines/baseline_offline.json

# 3. Chạy benchmark đa cấu hình chunking
python scripts/benchmark_chunking.py --domain-pack it-helpdesk

# 4. Chạy với Top-K tùy chỉnh và seed cố định
python scripts/eval_harness.py --offline -k 5 --seed 42 --compare baselines/baseline_offline.json
```
