# IT Helpdesk Multi-Agent AI System (Enterprise Production-Ready)

Hệ thống **IT Helpdesk Multi-Agent AI** thông minh, phân cấp 3 mức độ (3-Tier Support Architecture), tích hợp cơ chế bảo mật doanh nghiệp chuẩn **Enterprise SSO (Google OIDC + RBAC)**, tối ưu hóa chi phí và độ trễ với **BigQuery Serverless Vector Search** & **Semantic Cache Layer**, ứng dụng các công nghệ tiên tiến nhất của hệ sinh thái **Google AI (Google ADK, Gemini 3, Model Context Protocol - MCP, Vertex AI Memory Bank, Google Cloud Firestore)** và sẵn sàng triển khai trên **Google Cloud Run**.

---

## 🌟 1. Phân Cấp Hỗ Trợ Kỹ Thuật (3-Tier Multi-Agent Architecture)

```
                                  ┌─────────────────────────────┐
                                  │   Người dùng / Nhân viên    │
                                  └──────────────┬──────────────┘
                                                 │ (Bearer Google OIDC Token)
                                                 ▼
                                  ┌─────────────────────────────┐
                                  │ SSOAuthenticationMiddleware │
                                  │  (OIDC JWKS + Domain Filter)│
                                  └──────────────┬──────────────┘
                                                 │
                                                 ▼
                                  ┌─────────────────────────────┐
                                  │    Semantic Cache Layer     │ ──[ HIT (Sim >= 0.92) ]──► [ Trả lời tức thì < 50ms ]
                                  │    (Vector Cosine Match)    │                             (Tiết kiệm 100% Token)
                                  └──────────────┬──────────────┘
                                                 │ [ MISS ]
                                                 ▼
                                  ┌─────────────────────────────┐
                                  │  root_triage_orchestrator   │ ◄──► [ Vertex AI Memory Bank ]
                                  │      (Gemini 3 Flash)       │
                                  └──────────────┬──────────────┘
                 ┌───────────────────────────────┼──────────────────────────────┐
                 ▼                               ▼                              ▼
  ┌─────────────────────────────┐ ┌─────────────────────────────┐ ┌─────────────────────────────┐
  │    l1_selfservice_agent     │ │   l2_enterprise_rag_agent   │ │  l3_deep_diagnostics_agent  │
  │      (Gemini 3 Flash)       │ │      (Gemini 3 Flash)       │ │   (Gemini 3 Pro Preview)    │
  └──────────────┬──────────────┘ └──────────────┬──────────────┘ └──────────────┬──────────────┘
          ┌──────┴──────┐                 ┌──────┴──────┐                 ┌──────┴──────┐
          ▼             ▼                 ▼             ▼                 ▼             ▼
    [Ticketing]   [Memory Tool]     [Enterprise   [Email Draft]     [Log Analyzer  [Compliance
     (Firestore)                     RAG MCP]                         RCA Tool]     SLA Tool]
                                  (BigQuery/Mem)                     (RBAC Gate)   (RBAC Gate)
```

### 🟢 Mức 1 — Giao Tiếp & Hỗ Trợ Cơ Bản (`l1_selfservice_agent`)
- **FAQ & Chính sách IT:** Giải đáp chính sách bảo mật, chuẩn độ phức tạp mật khẩu, hướng dẫn kết nối VPN, Wi-Fi doanh nghiệp, cài đặt máy in.
- **Quy trình Tự phục vụ (Self-Service):** Hướng dẫn chi tiết từng bước khi người dùng cần reset mật khẩu Active Directory, Google Workspace, Okta, hoặc tự mở khóa tài khoản.
- **Tiếp nhận & Phân loại sự cố:** Lắng nghe mô tả lỗi, tự động tạo ticket với category và mức độ ưu tiên chính xác (`Low`, `Medium`, `High`, `Critical`).

### 🔵 Mức 2 — Tra Cứu Tài Liệu (RAG) & Hệ Thống Doanh Nghiệp (`l2_enterprise_rag_agent`)
- **Enterprise RAG MCP:** Tích hợp tra cứu sâu cơ sở tri thức hệ thống **ERP** (SAP/Oracle PO & kế toán), **HRM** (Workday chấm công & onboarding), **CRM** (Salesforce lead sync & quota).
- **Kiến trúc Adapter Linh hoạt:** Tự động chuyển đổi giữa `InMemoryKnowledgeStore` (cho local dev/unit test) và `BigQueryVectorKnowledgeStore` (cho Production Serverless Vector Search không tốn chi phí duy trì Index Endpoint cố định).
- **Đọc hiểu & Tóm tắt tài liệu dài:** Trích xuất các điểm mấu chốt và action items từ các tài liệu kỹ thuật dài.
- **Soạn thảo Email & Cập nhật Ticket:** Soạn bản thảo email phản hồi chuẩn mực, lịch sự và tự động đồng bộ tiến độ ticket.

### 🟣 Mức 3 — Phân Tích & Suy Luận Chuyên Sâu (`l3_deep_diagnostics_agent`)
- **Mô hình năng lực cao:** Sử dụng `gemini-3-pro-preview` chuyên trách suy luận logic và phân tích cấu trúc phức tạp.
- **Root Cause Analysis (RCA):** Phân tích log files, stack traces, phát hiện các mã lỗi trọng yếu (`OUT_OF_MEMORY`, `DB_CONNECTION_EXHAUSTED`, `NETWORK_TIMEOUT`, `AUTH_SECURITY_FAILURE`, `DISK_IO_FAILURE`, `DATA_CORRUPTION_NULL`) và lập báo cáo nguyên nhân gốc rễ cùng giải pháp khắc phục.
- **Pháp lý IT & Rà soát SLA Hợp đồng:** Trích xuất cam kết Uptime, thời gian phản hồi MTTR 2 chiều (prefix/suffix), điều khoản bồi thường (Service Credits), quyền kiểm toán an toàn thông tin và thông báo sự cố bảo mật (DPA/NDA/GDPR).

---

## ⚡ 2. Tối Ưu Hóa Hiệu Năng & Chi Phí (Semantic Cache & BigQuery Vector)

### A. Semantic Cache Layer (`semantic_cache.py`)
- **Vấn đề giải quyết:** Các câu hỏi IT Helpdesk lặp lại thường xuyên (ví dụ: *"hướng dẫn đổi pass wifi"*, *"cách thay đổi mật khẩu wifi văn phòng"*). Nếu mỗi câu hỏi đều gọi Gemini sẽ tốn chi phí token và mất 2-3s phản hồi.
- **Giải pháp:** Áp dụng **Vector Cosine Similarity ($\ge 0.92$)** kết hợp TTL Expiration và LRU Eviction:
  - **Cache Hit:** Trả kết quả ngay lập tức ($< 50\text{ms}$), giảm $100\%$ chi phí token Gemini.
  - **Cache Miss:** Chuyển tiếp vào Orchestrator xử lý bình thường và tự động ghi nhớ vào cache.
- **Endpoints Giám Sát:**
  - `GET /api/cache/stats`: Xem tỷ lệ hit rate, số lượng entry trong cache.
  - `GET /api/cache/query?q=...`: Tra cứu trực tiếp nội dung bộ nhớ đệm ngữ nghĩa.

### B. Serverless BigQuery Vector Search (`knowledge_store.py`)
- **So sánh với Vertex AI Vector Search:**
  - **Vertex AI Vector Search:** Đòi hỏi duy trì Index Endpoint chuyên dụng 24/7 (chi phí cố định ~$100–$300/tháng kể cả khi không có truy vấn).
  - **BigQuery Vector Search:** Serverless $100\%$, dùng hàm `VECTOR_SEARCH` hoặc `COSINE_DISTANCE` trực tiếp trên bảng BigQuery. Với quy mô $< 100\text{k}$ vectors, chi phí duy trì gần như **0 USD/tháng**, cực kỳ linh hoạt và dễ quản lý qua SQL.

---

## 🔒 3. Kiến Trúc Bảo Mật Doanh Nghiệp (Enterprise Security & SSO)

Hệ thống được thiết kế theo tiêu chuẩn an toàn thông tin cấp doanh nghiệp, khắc phục hoàn toàn các lỗ hổng bảo mật phổ biến:

| Cơ chế bảo mật | Chi tiết kỹ thuật | Trạng thái bảo vệ |
| :--- | :--- | :--- |
| **Xác thực Google OIDC Chuẩn** | Sử dụng `google.oauth2.id_token.verify_oauth2_token` kiểm tra chữ ký số qua JWKS public certs của Google (`accounts.google.com`). | ✅ **Strict OIDC** |
| **Fail-Closed Domain Filtering** | Bắt buộc cấu hình `ALLOWED_DOMAINS` trên Production. Ngăn chặn triệt để tài khoản cá nhân `@gmail.com` truy cập hệ thống. | ✅ **Fail-Closed** |
| **Cô Lập Thuật Toán (No Confusion)** | RS256 chỉ dành cho token Google OIDC; HS256 chỉ dùng cho Dev Mock Token. Tuyệt đối không cho phép dùng chung secret key. | ✅ **Algorithm Isolation** |
| **Connection Pooling & Cache JWKS** | Singleton `Request` adapter kết hợp `requests.Session()` tái sử dụng connection pool HTTPS, giảm thiểu latency xác thực. | ✅ **High Performance** |
| **Bảo Vệ Toàn Diện Middleware** | `SSOAuthenticationMiddleware` bảo vệ mọi endpoint (ADK agent, API, session) ngoại trừ các public endpoint (`/healthz`, `/docs`). | ✅ **Zero Trust Per-Route** |
| **Phân Quyền RBAC (Role-Based)** | Sử dụng `ContextVar` truyền context người dùng vào các tool nhạy cảm (L3 RCA, Compliance SLA), chặn truy cập trái phép từ user thường. | ✅ **Granular RBAC** |

### Ma Trận Phân Quyền (RBAC Matrix)

| Vai trò (Role) | L1 Self-Service | L2 Enterprise RAG | L3 Log RCA Tool | L3 Contract SLA Tool |
| :--- | :---: | :---: | :---: | :---: |
| **`employee`** | ✅ Cho phép | ✅ Cho phép | ❌ Bị từ chối (`forbidden`) | ❌ Bị từ chối (`forbidden`) |
| **`it_admin` / `sys_admin`** | ✅ Cho phép | ✅ Cho phép | ✅ Cho phép | ✅ Cho phép |
| **`compliance_officer` / `legal_counsel`** | ✅ Cho phép | ✅ Cho phép | ❌ Bị từ chối | ✅ Cho phép |
| **`devops_engineer` / `lead_engineer`** | ✅ Cho phép | ✅ Cho phép | ✅ Cho phép | ❌ Bị từ chối |

---

## 💾 4. Cơ Chế Lưu Trữ Dữ Liệu (Persistence & State Management)

1. **Hệ thống Quản lý Ticket (`ticketing_tool.py`):**
   - Tích hợp **Google Cloud Firestore** (`collection: helpdesk_tickets`) hỗ trợ mở rộng không giới hạn khi triển khai trên multi-instance Cloud Run.
   - Cơ chế write-through cache và fallback tự động sang in-memory khi chạy local dev hoặc test offline.
2. **Trí nhớ dài hạn (`agent.py`):**
   - Tích hợp **Vertex AI Memory Bank** (`VertexAiMemoryBankService`) tự động lưu vết ngữ cảnh người dùng, lịch sử thiết bị và sự cố lặp lại.
3. **Cơ sở tri thức (`knowledge_store.py`):**
   - Hỗ trợ lưu trữ và truy vấn vector trên **Google BigQuery** dataset `it_helpdesk_kb`.

---

## ⚙️ 5. Cấu Hình Biến Môi Trường (Environment Variables)

Sao chép file cấu hình mẫu:
```bash
cp .env.example .env
```

| Tên biến | Bắt buộc (Prod) | Mặc định | Ý nghĩa |
| :--- | :---: | :---: | :--- |
| `ENVIRONMENT` | Có | `development` | Môi trường: `development`, `staging`, `production`. |
| `GOOGLE_CLOUD_PROJECT` | Có | — | ID của dự án Google Cloud. |
| `GOOGLE_CLOUD_REGION` | Có | `us-central1` | Vùng triển khai GCP (ví dụ: `us-central1`, `asia-southeast1`). |
| `SSO_CLIENT_ID` | Có | — | OAuth 2.0 Client ID được cấp từ GCP Console. |
| `ALLOWED_DOMAINS` | **Bắt buộc** | — | Danh sách domain email công ty được phép đăng nhập (ví dụ: `company.com,corp.com`). |
| `ALLOW_LOCAL_DEV_SSO` | Không | `false` | Bật tạo/kiểm tra mock token cho local dev (luôn bị tắt trong Prod). |
| `SSO_JWT_SECRET` | Không | — | Khóa bí mật chỉ dùng cho Dev Mock Token. |
| `USE_FIRESTORE_TICKETS`| Không | `false` | Bật Firestore backend cho ticket storage (tự động bật trên Cloud Run). |
| `KNOWLEDGE_BACKEND` | Không | `in_memory` | Backend cho RAG (`in_memory` hoặc `bigquery`). |
| `BIGQUERY_KB_DATASET` | Không | `it_helpdesk_kb`| Dataset BigQuery chứa tài liệu tri thức doanh nghiệp. |
| `SEMANTIC_CACHE_ENABLED`| Không| `true` | Bật lớp bộ đệm ngữ nghĩa cho câu hỏi lặp lại. |
| `SEMANTIC_CACHE_THRESHOLD`| Không| `0.92` | Ngưỡng tương đồng cosine để coi là trùng khớp câu hỏi. |
| `OTEL_TO_CLOUD` | Không | `false` | Đẩy trace/metrics lên Google Cloud Monitoring/Trace. |

---

## 🚀 6. Hướng Dẫn Cài Đặt & Chạy Cục Bộ (Local Development)

### Bước 1: Cài đặt Dependencies với `uv`
```bash
uv sync
```

### Bước 2: Chạy Bộ Kiểm Thử Toàn Diện (Unit Tests)
```bash
uv run pytest tests/ -v
```
*(Hiện tại toàn bộ 46/46 test cases đều vượt qua 100%)*

### Bước 3: Chạy Tương Tác Cục Bộ (CLI Mode)
```bash
uv run python main.py --mode cli
```

### Bước 4: Khởi Chạy Web Server (FastAPI + ADK Web UI)
```bash
uv run python main.py --mode serve --port 8080
```
- Giao diện Web: `http://localhost:8080`
- OpenAPI Swagger Docs: `http://localhost:8080/docs`
- Healthcheck Endpoint: `http://localhost:8080/healthz`
- Semantic Cache Stats: `http://localhost:8080/api/cache/stats`

---

## ☁️ 7. Triển Khai Lên Google Cloud Run (Production)

### Bước 1: Khởi Tạo Hạ Tầng Tự Động (Terraform)
Thư mục `deployment/terraform` đã cấu hình sẵn:
- Service Account với nguyên tắc đặc quyền tối thiểu (Least Privilege).
- Secret Manager, Artifact Registry, BigQuery Dataset, Cloud Run v2 Service.
- Lifecycle `ignore_changes = [template[0].containers[0].image]` chống drift cấu hình khi CI/CD cập nhật image mới.

```bash
cd deployment/terraform
terraform init
terraform plan \
  -var="project_id=YOUR_PROJECT_ID" \
  -var="sso_client_id=YOUR_OAUTH_CLIENT_ID.apps.googleusercontent.com" \
  -var="allowed_domains=company.com"

terraform apply -auto-approve \
  -var="project_id=YOUR_PROJECT_ID" \
  -var="sso_client_id=YOUR_OAUTH_CLIENT_ID.apps.googleusercontent.com" \
  -var="allowed_domains=company.com"
cd ../..
```

### Bước 2: Build Container & Triển Khai Cloud Run
```bash
make docker-deploy PROJECT_ID=YOUR_PROJECT_ID REGION=us-central1
```

---

## 📂 8. Cấu Trúc Mã Nguồn (Project Structure)

```
it-helpdesk-agent/
├── .env.example                     # File mẫu biến môi trường
├── Dockerfile                       # Container definition chuẩn production
├── Makefile                         # Lệnh tiện ích cho build, test, deploy
├── README.md                        # Tài liệu hướng dẫn toàn diện
├── pyproject.toml                   # Định nghĩa dependencies và project metadata
├── main.py                          # Entrypoint khởi chạy CLI hoặc Fast-API server
├── test_local.py                    # Script chạy thử nghiệm tương tác runner
├── deployment/
│   └── terraform/                   # Infrastructure-as-Code cho GCP
│       ├── main.tf                  # Định nghĩa Cloud Run, BigQuery, IAM, Secrets
│       └── variables.tf             # Biến cấu hình Terraform
├── it_helpdesk_agent/
│   ├── agent.py                     # Cấu hình Multi-Agent 3 cấp bậc (L1, L2, L3)
│   ├── fast_api_app.py              # Ứng dụng FastAPI, Middleware và Cache endpoints
│   ├── app_utils/
│   │   ├── env.py                   # Quản lý nạp biến môi trường & Secret Manager
│   │   ├── semantic_cache.py        # Semantic Cache Layer (Cosine Similarity, LRU, TTL)
│   │   └── sso_auth.py              # Xác thực OIDC JWKS, RBAC ContextVar & Middleware
│   └── tools/
│       ├── compliance_tool.py       # Công cụ phân tích SLA & hợp đồng IT (RBAC)
│       ├── log_analyzer.py          # Công cụ phân tích log lỗi Root Cause Analysis (RBAC)
│       ├── mcp_config.py            # Cấu hình Toolset Enterprise RAG MCP
│       ├── ticketing_tool.py        # Quản lý Ticket (Hỗ trợ Firestore + fallback cache)
│       └── enterprise_rag_mcp/      # Máy chủ Model Context Protocol (MCP) nội bộ
│           ├── knowledge_store.py   # BaseKnowledgeStore (InMemory + BigQuery Vector Search)
│           ├── main.py              # Server MCP FastMCP
│           └── rag_models.py        # Schemas dữ liệu RAG
└── tests/
    └── unit/                        # Bộ kiểm thử tự động (46 test cases)
        ├── test_agent_hierarchy.py  # Test cấu trúc phân cấp agent và model
        ├── test_compliance_tool.py  # Test trích xuất SLA 2 chiều & RBAC
        ├── test_enterprise_rag.py   # Test MCP tra cứu tri thức
        ├── test_env.py              # Test nạp secret & env
        ├── test_knowledge_store_adapters.py # Test Adapter Pattern & BigQuery Store
        ├── test_log_analyzer.py     # Test nhận diện lỗi OOM, DB, Disk, Null & RBAC
        ├── test_semantic_cache.py   # Test Cosine Similarity, Cache Hit/Miss, TTL, LRU
        ├── test_sso_auth.py         # Test OIDC JWKS, Fail-closed domain, RBAC, Middleware
        └── test_ticketing_tool.py   # Test tạo, cập nhật, chuyển tiếp ticket
```
