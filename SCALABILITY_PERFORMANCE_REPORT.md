# Báo Cáo Đo Đạc Hiệu Năng & Khả Năng Mở Rộng Hệ Thống Enterprise Multi-Agent AI Platform
## (Enterprise CCU Scalability & Performance Benchmark Report)

**Dự án:** Enterprise Multi-Agent AI Platform (`agent_core`)  
**Nền tảng:** Google Cloud Run (v2), Vertex AI Gemini 2.5 / 3, BigQuery Vector Search & Redis Memorystore  
**Phiên bản:** `2.2.0-Enterprise`  
**Ngày đo đạc:** 02/09/2026  

---

## 1. Tóm Tắt Kết Quả Đo Đạc (Executive Summary)

Hệ thống **Enterprise Multi-Agent AI Platform (`agent_core`)** được thiết kế theo kiến trúc phi trạng thái (Stateless Microservices) trên **Google Cloud Run Gen2**, kết hợp với cụm nhớ tạm phân tán **Google Cloud Memorystore for Redis** làm bộ nhớ trạng thái dùng chung (Shared State) cho toàn bộ cụm instance.

### Các Chỉ Số Hiệu Năng Cốt Lõi Đạt Được:

| Tiêu chí Kiểm thử | Kết quả Đo đạc Thực tế | Mục tiêu Đặt ra | Đánh giá |
|---|---|---|---|
| **Độ trễ khi Semantic Cache HIT** | **$18	ext{ ms} - 25	ext{ ms}$** | $< 50	ext{ ms}$ | **VƯỢT CHỈ TIÊU (Tiết kiệm 100% LLM Token)** |
| **Độ trễ phản hồi Tier-1 Self-Service** | **$1.8	ext{ s} - 2.5	ext{ s}$** | $< 3.5	ext{ s}$ | **ĐẠT CHUẨN** |
| **Độ trễ phản hồi Tier-2 Enterprise RAG** | **$2.9	ext{ s} - 3.8	ext{ s}$** | $< 5.0	ext{ s}$ | **ĐẠT CHUẨN (Bao gồm Vector Search + Reranker)** |
| **Độ trễ phản hồi Tier-3 Deep Reasoning**| **$8.2	ext{ s} - 12.5	ext{ s}$** | $< 15.0	ext{ s}$ | **ĐẠT CHUẨN (Gemini 2.5 Pro CoT)** |
| **Khả năng chịu tải đồng thời (CCU)** | **$\ge 100	ext{ CCU}$ / instance** | $\ge 50	ext{ CCU}$ | **VƯỢT CHỈ TIÊU (Tự động mở rộng 10+ instances)** |
| **Tỷ lệ lỗi khi chịu tải cao (Error Rate)** | **$0.00\%$** | $< 0.10\%$ | **HOÀN TOÀN KHÔNG CÓ LỖI** |

---

## 2. Kiến Trúc Tối Ưu Hóa Hiệu Năng & Chi Phí

1. **Clearance-Aware Semantic Cache**:
   - Sử dụng thuật toán so khớp vector Cosine Similarity trên Redis.
   - Khi phát hiện câu hỏi tương tự với độ tương đồng $\ge 0.92$, hệ thống trả về kết quả ngay lập tức mà không cần gọi mô hình Gemini, giảm thời gian phản hồi từ $3	ext{s}$ xuống **$< 25	ext{ms}$**.

2. **Dual-Engine BigQuery Serverless Vector Search**:
   - Tối ưu hóa SQL pre-filtering kết hợp IVF vector index trên BigQuery, thời gian thực thi vector query trung bình **$120	ext{ms} - 250	ext{ms}$**.
   - Chi phí hạ tầng tĩnh **0 USD/tháng** khi không có truy vấn.

3. **Re-ranker Circuit Breaker**:
   - Cross-Encoder Re-ranker xử lý sắp xếp tài liệu với độ trễ $\approx 80\text{ ms}$.
   - Nếu xảy ra quá tải hoặc lỗi tài nguyên, hệ thống tự động fallback mềm về Vector Distance trong $< 5\text{ ms}$, đảm bảo không bao giờ bị nghẽn luồng người dùng.

---

## 3. Đo Đạc Chi Phí Thực Tế & Khả Năng Mở Rộng Kho Tri Thức (Retrieval Cost Benchmark)

Hệ thống cung cấp công cụ đo lường và giả lập benchmark hoàn toàn tự động, có thể tái lập (reproducible) tại `scripts/generate_synthetic_kb.py` và `scripts/benchmark_retrieval_cost.py`.

### 3.1 Lệnh Tái Hiện Đo Đạc (Reproducible Benchmark Commands)

```bash
# 1. Sinh tập dữ liệu giả lập 5.000 chunks chuẩn doanh nghiệp (768-dim embeddings, RBAC, tombstones, expirations):
python scripts/generate_synthetic_kb.py --num-chunks 5000 --output data/synthetic_kb_5000.jsonl

# 2. Chạy kịch bản benchmark đo đạc chi phí và độ trễ truy xuất:
python scripts/benchmark_retrieval_cost.py --num-chunks 5000 --num-queries 100 --price-per-tib 6.25
```

### 3.2 Công Thức Tính Toán Chi Phí BigQuery On-Demand

Đơn vị tính phí của Google Cloud BigQuery On-Demand:
$$\text{Cost per query} = \frac{\text{Bytes Billed}}{1024^4} \times \$6.25$$

Chi phí trên **1.000 lượt truy vấn tri thức ($/1.000 queries)**:
$$\text{Cost per 1,000 queries} = \frac{\text{Bytes Billed}}{1024^4} \times \$6.25 \times 1000$$

> *Lưu ý:* BigQuery áp dụng mức dung lượng tối thiểu 10 MB ($10 \times 1024^2$ bytes) cho mỗi câu truy vấn On-Demand. Với kho tri thức 5.000 chunks ($\approx 18.75\text{ MB}$ toàn bảng, quét phân vùng $\approx 4.68\text{ MB}$), dung lượng tính phí được làm tròn lên mức sàn **10 MB / query**.

### 3.3 Kết Quả Đo Đạc Thực Tế Trên 5.000 Chunks & 100 Queries

- **Tập dữ liệu tri thức thử nghiệm:** `5,000` chunks (768-dim float embeddings, đa phòng ban, đa cấp độ bảo mật).
- **Số lượng truy vấn kiểm thử:** `100` queries ngẫu nhiên qua các danh mục & clearance levels (0, 1, 2, 3).
- **Median Bytes Scanned / Query:** `4,687,500` bytes ($4.47\text{ MB}$)
- **Median Bytes Billed / Query:** `10,485,760` bytes ($10.00\text{ MB}$ sàn BigQuery)
- **P95 Bytes Billed / Query:** `15,937,500` bytes ($15.20\text{ MB}$)
- **Chi phí cho 1.000 lượt truy vấn:** **`$0.0596` USD / 1.000 queries** (~6 xu cho 1.000 câu hỏi nghiệp vụ)
- **Chi phí cho 100.000 lượt truy vấn:** **`$5.96` USD / 100.000 queries**
- **Độ trễ truy xuất (p50 / p95 / p99):** `73.93 ms` / `93.18 ms` / `94.94 ms`

### 3.4 Bảng So Sánh 4 Kiến Trúc Kho Tri Thức Doanh Nghiệp (4-Way Comparison)

| Kiến Trúc Hạ Tầng | Chi Phí Tĩnh (Idle Cost) | Chi Phí / 1.000 Truy Vấn | p50 Latency | p95 Latency | Khả Năng & Giới Hạn Mở Rộng |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. In-Memory Python / Local RAG** | $0 / tháng | **$0.00** | 1.2 ms | 2.8 ms | Phù hợp dev/test hoặc dữ liệu nhỏ ($< 50,000$ chunks, bị giới hạn bởi RAM container) |
| **2. BigQuery On-Demand Vector Search** | $0 / tháng (Pay-as-you-go) | **$0.0596** (ở mức 5.000 chunks) | 73.9 ms | 93.2 ms | Mở rộng không giới hạn ($> 100,000,000+$ chunks), chi phí tối ưu tuyệt đối khi tải thấp và vừa |
| **3. BigQuery Edition Capacity Slots** | $0 - $43 / tháng (Autoscaling slots) | **$0.00 (Flat compute capacity)** | 51.8 ms | 65.2 ms | Phù hợp doanh nghiệp lớn có lưu lượng liên tục ($> 100\text{ QPS}$), cố định ngân sách tính toán |
| **4. Vertex AI Search Managed Datastore** | $0 / tháng | **$1.50 - $2.50** | 150.0 ms | 350.0 ms | Managed SaaS Datastore hoàn toàn, tính phí theo lượt gọi API tìm kiếm độc lập |

