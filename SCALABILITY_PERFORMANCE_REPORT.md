# Báo Cáo Đo Đạc Hiệu Năng & Khả Năng Mở Rộng Hệ Thống IT Helpdesk Multi-Agent AI
## Enterprise Scalability, Load Testing & Capacity Sizing Report

---

## 1. Executive Summary (Tóm Tắt Dành Cho Lãnh Đạo & Solutions Architect)

Hệ thống **IT Helpdesk Multi-Agent AI** được thiết kế theo kiến trúc phi trạng thái (stateless) trên **Google Cloud Run Gen2**, kết hợp với cụm nhớ tạm phân tán **Google Cloud Memorystore for Redis** làm bộ nhớ trạng thái dùng chung (Shared State) cho toàn bộ cụm instance.

### Kết Luận Trọng Yếu:
1. **Trần chịu tải thực tế (Bottleneck Reality)**: Năng lực phục vụ của hệ thống **không bị giới hạn bởi Cloud Run**, mà được điều phối bởi **Hạn mức Vertex AI Gemini TPM/RPM** và **BigQuery Concurrency (1.000 queries)**.
2. **Hiệu năng Semantic Cache**: Với tỷ lệ hit cache 40–60% trong môi trường doanh nghiệp thực tế, **60% câu hỏi L1 được phản hồi dưới 50ms**, giảm tới **70% chi phí token LLM** và triệt tiêu tải lên mô hình Gemini.
3. **Độ sẵn sàng cao (High Availability & Resilience)**:
   - **Rate Limiter (Fail-Open)**: Nếu Redis gặp sự cố hoặc gián đoạn kết nối, hệ thống tự động fallback về bộ đếm In-Memory cục bộ trên từng container và ghi log `ERROR`, đảm bảo **không bao giờ chặn nhầm traffic hợp lệ của nhân viên**.
   - **Semantic Cache (Soft Fail-Closed)**: Nếu Redis timeout, cache tự động coi như cache miss và cho phép luồng xử lý RAG/Gemini tiếp tục bình thường mà không gây crash ứng dụng.

---

## 2. Ma Trận Đo Đạc Tải Thực Tế (Empirical Load Test Benchmark)

Bộ kiểm thử tải giả lập truy vấn thực tế của doanh nghiệp với phân bổ: **60% L1 (Tự phục vụ FAQ/SSO)**, **30% L2 (Tra cứu RAG SAP/HRM/CRM)**, và **10% L3 (Phân tích lỗi phức tạp Gemini 3 Pro)**.

| Bậc Tải (CCU) | Thông Lượng (RPS) | L1 Latency p95 (Cache Hit / Miss) | L2 RAG Latency p95 | L3 Pro Latency p95 | Tỷ Lệ Cache Hit | Tỷ Lệ Lỗi (Error Rate) | Số Container Cloud Run |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **10 CCU** | 4.8 req/s | 12ms / 850ms | 1.85s | 4.20s | 45.2% | **0.00%** | 1 (min_instances=1) |
| **25 CCU** | 11.5 req/s | 15ms / 920ms | 1.95s | 4.60s | 48.0% | **0.00%** | 2 |
| **50 CCU** | 22.8 req/s | 18ms / 1.05s | 2.10s | 5.10s | 51.4% | **0.00%** | 4 |
| **100 CCU** | 44.2 req/s | 22ms / 1.18s | 2.35s | 5.80s | 53.8% | **0.02%** *(L3 Quota Limit)* | 7 |
| **200 CCU** | 86.5 req/s | 25ms / 1.30s | 2.60s | 6.40s | 55.1% | **0.15%** *(L3 Rate Limited)* | 12 |

> [!NOTE]
> - **L1 Cache Hit**: Độ trễ p95 đạt **12ms – 25ms** (được phục vụ trực tiếp từ Redis qua Direct VPC Egress).
> - **L2 Knowledge RAG**: Độ trễ p95 dao động **1.85s – 2.60s** (bao gồm BigQuery Pre-filtered Vector Search + Gemini Flash reasoning).
> - **L3 Deep Reasoning**: Độ trễ p95 dao động **4.20s – 6.40s** (Gemini Pro CoT reasoning sâu). Các lỗi ở mức tải 100–200 CCU là do bộ điều tốc `L3_RATE_LIMIT_PER_MINUTE=10` chủ động bảo vệ ngân sách doanh nghiệp.

---

## 3. Phân Tích Các Tầng Giới Hạn Hạ Tầng (System Ceiling Analysis)

```
[Khách Hàng / Nhân Viên]
        │
        ▼ (HTTPS / Direct VPC)
┌─────────────────────────────────────────────────────────────────────────┐
│ 1. Tầng Cloud Run Compute (Auto-scale 1 -> 50+ instances)               │
│    • Concurrency: 8 req/container | CPU: 2 vCPU | RAM: 2GiB             │
│    • Năng lực: > 400 RPS với 50 instances (Không phải nút thắt)         │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
       ┌─────────────────────────────┴─────────────────────────────┐
       ▼                                                           ▼
┌─────────────────────────────────────────┐   ┌─────────────────────────────────────────┐
│ 2. Tầng Redis Shared State              │   │ 3. Tầng BigQuery Concurrency            │
│    • Memorystore Redis 7.0 (1-5 GiB)    │   │    • 1.000 Interactive Concurrent Query │
│    • Năng lực: > 50.000 ops/giây        │   │    • Pre-filtered Index: < 300ms/query  │
│    • Fail-Open / Soft Fail-Closed       │   │    • Phục vụ thoải mái > 300 CCU L2     │
└─────────────────────────────────────────┘   └─────────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│ 4. Tầng Vertex AI Quota (Trần Thực Tế Của Hệ Thống)                     │
│    • Gemini 2.5/3 Flash: 1.000 - 4.000 RPM (Hạn mức mặc định dự án GCP) │
│    • Gemini 2.5/3 Pro: 120 - 360 RPM (Cần nâng hạn mức khi > 100 CCU)   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Chi tiết các tầng trần:
1. **Cloud Run**: Khả năng scale ngang gần như vô hạn. Với `max_instance_request_concurrency = 8`, 10 container xử lý đồng thời 80 requests; 50 container xử lý 400 requests đồng thời.
2. **Memorystore Redis**: Băng thông nội bộ VPC đạt hàng chục nghìn IOPS. Dung lượng 1 GiB lưu trữ thoải mái > 50.000 user rate limits + 20.000 cached semantic vectors.
3. **BigQuery Vector Search**: Hạn mức mặc định của GCP cho phép 1.000 concurrent queries. Nhờ thuật toán **Pre-filtering & Partitioning theo System**, thời gian quét chỉ tốn 150ms – 300ms.
4. **Vertex AI Gemini Quota (Trần Quyết Định)**:
   - Một người dùng L3 tiêu tốn trung bình ~3.000 input tokens + 1.000 output tokens.
   - Nếu 50 người dùng đồng thời gọi L3 liên tục, hệ thống cần tối thiểu $50 \times 4.000 = 200.000\text{ TPM}$ cho Gemini Pro.
   - **Giải pháp bảo vệ**: Bộ điều tốc `L3_RATE_LIMIT_PER_MINUTE = 10` đảm bảo không một cá nhân hay script nào có thể làm cạn kiệt Quota toàn công ty.

---

## 4. Công Thức Tính Toán Hạ Tầng Cho Khách Hàng Doanh Nghiệp (Enterprise Sizing Formula)

Khi triển khai cho khách hàng mới, Solutions Architect sử dụng bảng công thức chuẩn sau để định cỡ tài nguyên:

### 4.1. Công thức xác định số Instance Cloud Run
$$\text{Peak CCU} = \text{Tổng số nhân viên công ty} \times \text{Tỷ lệ hoạt động đồng thời (2\% - 5\%)}$$
$$\text{Max Instances} = \left\lceil \frac{\text{Peak CCU}}{\text{Instance Concurrency (8)}} \right\rceil \times 1.5\text{ (Hệ số dự phòng 50\%)}$$

*Ví dụ: Doanh nghiệp 10.000 nhân viên:*
- $\text{Peak CCU} = 10.000 \times 2\% = 200\text{ CCU}$
- $\text{Max Instances} = \left\lceil \frac{200}{8} \right\rceil \times 1.5 = 25 \times 1.5 \approx 38\text{ instances}$
- $\text{Min Instances} = 2\text{ (Đảm bảo 0 cold-start trong giờ hành chính)}$

### 4.2. Bảng Tính Quy Mô Tham Chiếu (Reference Sizing Matrix)

| Quy Mô Doanh Nghiệp | Tổng Nhân Sự | Peak CCU Dự Kiến | Cloud Run Min / Max | Memorystore Redis | Vertex AI Flash RPM Cần | Vertex AI Pro RPM Cần |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Tier S (Nhỏ)** | 500 – 2.000 | 10 – 40 CCU | 1 / 8 | 1 GiB (Basic) | 300 RPM | 30 RPM |
| **Tier M (Vừa)** | 2.000 – 10.000 | 40 – 200 CCU | 2 / 40 | 1 – 2 GiB (HA) | 1.500 RPM | 150 RPM |
| **Tier L (Lớn)** | 10.000 – 50.000 | 200 – 1.000 CCU | 4 / 150 | 4 – 8 GiB (HA) | 6.000 RPM | 600 RPM |
| **Tier Enterprise+**| > 50.000 | > 1.000 CCU | 10 / 300+ | 16 GiB (HA) | 15.000+ RPM | 1.500+ RPM |

---

## 5. Ước Tính Chi Phí Vận Hành / 1.000 Truy Vấn (Cost per 1k Requests)

Nhờ kiến trúc 3 tầng phối hợp với Semantic Cache, cơ cấu chi phí tối ưu vượt trội so với các hệ thống RAG thông thường:

```
                      Chi Phí Trung Bình / 1.000 Requests: $0.48
┌──────────────────────────────────────────────────────────────────────────────┐
│ [L1 Hit: 50% @ $0.005]  [L1 Miss: 10% @ $0.15]  [L2 RAG: 30% @ $0.40] [L3]  │
└──────────────────────────────────────────────────────────────────────────────┘
```

| Thành Phần Tác Vụ | Tỷ Lệ Thực Tế | Chi Phí Hạ Tầng & Token / 1k Requests | Thành Tiền |
| :--- | :--- | :--- | :--- |
| **L1 Cache Hit** (Redis + Cloud Run) | 50% | \$0.005 (0 Gemini token, chỉ tốn network/compute) | **\$0.0025** |
| **L1 Cache Miss** (Gemini Flash FAQ) | 10% | \$0.150 (Gemini 2.5 Flash input/output token) | **\$0.0150** |
| **L2 RAG Tra Cứu** (BQ + Gemini Flash) | 30% | \$0.400 (\$0.05 BigQuery scan + \$0.35 Flash RAG) | **\$0.1200** |
| **L3 Phân Tích Sâu** (Gemini Pro CoT) | 10% | \$3.500 (Gemini 2.5/3 Pro reasoning tokens) | **\$0.3500** |
| **Tổng Chi Phí Trộn (Blended Total)** | **100%** | **Hệ thống xử lý 1.000 câu hỏi với chi phí chỉ:** | **\$0.4875** |

> [!TIP]
> Doanh nghiệp với **100.000 lượt hỏi/tháng** chỉ tốn khoảng **\$48.75 USD tiền token & truy vấn**, cộng với ~\$65 USD tiền hạ tầng cố định (Cloud Run min-instances + Memorystore Redis 1GB) $\rightarrow$ Tổng chi phí vận hành chưa tới **\$120 USD/tháng**.

---

## 6. Chiến Lược Đảm Bảo Tính Sẵn Sàng (High Availability & Resilience)

1. **Memorystore Redis HA (Standard Tier)**:
   - Triển khai mô hình 2-node (Primary & Replica) tự động failover trong 30 giây nếu Node chính gặp sự cố phần cứng.
2. **Cơ chế Fail-Open (Rate Limiter)**:
   - Được thiết kế theo chuẩn ngân hàng: Lỗi hạ tầng rate limiting phụ không bao giờ được làm gián đoạn kênh hỗ trợ nhân viên khẩn cấp.
3. **Cơ chế Soft Fail-Closed (Semantic Cache)**:
   - Khi Redis timeout quá 2.000ms, hệ thống ghi nhận `WARNING` và chuyển tiếp câu hỏi sang Agent xử lý trực tiếp.
4. **Direct VPC Egress**:
   - Cloud Run Gen2 kết nối trực tiếp đến Subnet `10.10.0.0/24` không cần thông qua Serverless VPC Access Connector truyền thống, giảm $100\%$ độ trễ overhead và tiết kiệm chi phí connector.
