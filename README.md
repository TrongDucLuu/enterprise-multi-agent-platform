# IT Helpdesk Multi-Agent AI System (Enterprise Production-Ready)

Hệ thống **IT Helpdesk Multi-Agent AI** thông minh, phân cấp 3 mức độ (3-Tier Support Architecture), tích hợp cơ chế bảo mật doanh nghiệp chuẩn **Enterprise SSO (Google OIDC + RBAC)**, ứng dụng các công nghệ tiên tiến nhất của hệ sinh thái **Google AI (Google ADK, Gemini 3, Model Context Protocol - MCP, Vertex AI Memory Bank, Google Cloud Firestore)** và sẵn sàng triển khai trên **Google Cloud Run**.

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
                                                 │ (ContextVar SSOUser)
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
                                                                     (RBAC Gate)   (RBAC Gate)
```

### 🟢 Mức 1 — Giao Tiếp & Hỗ Trợ Cơ Bản (`l1_selfservice_agent`)
- **FAQ & Chính sách IT:** Giải đáp chính sách bảo mật, chuẩn độ phức tạp mật khẩu, hướng dẫn kết nối VPN, Wi-Fi doanh nghiệp, cài đặt máy in.
- **Quy trình Tự phục vụ (Self-Service):** Hướng dẫn chi tiết từng bước khi người dùng cần reset mật khẩu Active Directory, Google Workspace, Okta, hoặc tự mở khóa tài khoản.
- **Tiếp nhận & Phân loại sự cố:** Lắng nghe mô tả lỗi, tự động tạo ticket với category và mức độ ưu tiên chính xác (`Low`, `Medium`, `High`, `Critical`).

### 🔵 Mức 2 — Tra Cứu Tài Liệu (RAG) & Hệ Thống Doanh Nghiệp (`l2_enterprise_rag_agent`)
- **Enterprise RAG MCP:** Tích hợp tra cứu sâu cơ sở tri thức hệ thống **ERP** (SAP/Oracle PO & kế toán), **HRM** (Workday chấm công & onboarding), **CRM** (Salesforce lead sync & quota).
- **Đọc hiểu & Tóm tắt tài liệu dài:** Trích xuất các điểm mấu chốt và action items từ các tài liệu kỹ thuật dài.
- **Soạn thảo Email & Cập nhật Ticket:** Soạn bản thảo email phản hồi chuẩn mực, lịch sự và tự động đồng bộ tiến độ ticket.

### 🟣 Mức 3 — Phân Tích & Suy Luận Chuyên Sâu (`l3_deep_diagnostics_agent`)
- **Mô hình năng lực cao:** Sử dụng `gemini-3-pro-preview` chuyên trách suy luận logic và phân tích cấu trúc phức tạp.
- **Root Cause Analysis (RCA):** Phân tích log files, stack traces, phát hiện các mã lỗi trọng yếu (`OUT_OF_MEMORY`, `DB_CONNECTION_EXHAUSTED`, `NETWORK_TIMEOUT`, `AUTH_SECURITY_FAILURE`, `DISK_IO_FAILURE`, `DATA_CORRUPTION_NULL`) và lập báo cáo nguyên nhân gốc rễ cùng giải pháp khắc phục.
- **Pháp lý IT & Rà soát SLA Hợp đồng:** Trích xuất cam kết Uptime, thời gian phản hồi MTTR 2 chiều (prefix/suffix), điều khoản bồi thường (Service Credits), quyền kiểm toán an toàn thông tin và thông báo sự cố bảo mật (DPA/NDA/GDPR).

---

## 🔒 2. Kiến Trúc Bảo Mật Doanh Nghiệp (Enterprise Security & SSO)

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

## 💾 3. Cơ Chế Lưu Trữ Dữ Liệu (Persistence & State Management)

1. **Hệ thống Quản lý Ticket (`ticketing_tool.py`):**
   - Tích hợp **Google Cloud Firestore** (`collection: helpdesk_tickets`) hỗ trợ mở rộng không giới hạn khi triển khai trên multi-instance Cloud Run.
   - Cơ chế write-through cache và fallback tự động sang in-memory khi chạy local dev hoặc test offline.
2. **Trí nhớ dài hạn (`agent.py`):**
   - Tích hợp **Vertex AI Memory Bank** (`VertexAiMemoryBankService`) tự động lưu vết ngữ cảnh người dùng, lịch sử thiết bị và sự cố lặp lại.

---

## ⚙️ 4. Cấu Hình Biến Môi Trường (Environment Variables)

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
| `OTEL_TO_CLOUD` | Không | `false` | Đẩy trace/metrics lên Google Cloud Monitoring/Trace. |

---

## 🚀 5. Hướng Dẫn Cài Đặt & Chạy Cục Bộ (Local Development)

### Bước 1: Cài đặt Dependencies với `uv`
```bash
uv sync
```

### Bước 2: Chạy Bộ Kiểm Thử Toàn Diện (Unit Tests)
```bash
uv run pytest tests/ -v
```
*(Hiện tại 37/37 test cases đều vượt qua 100%)*

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

---

## ☁️ 6. Triển Khai Lên Google Cloud Run (Production)

### Bước 1: Khởi Tạo Hạ Tầng Tự Động (Terraform)
Thư mục `deployment/terraform` đã cấu hình sẵn:
- Service Account với nguyên tắc đặc quyền tối thiểu (Least Privilege).
- Secret Manager, Artifact Registry, Cloud Run v2 Service.
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

## 📂 7. Cấu Trúc Mã Nguồn (Project Structure)

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
│       ├── main.tf                  # Định nghĩa tài nguyên GCP Cloud Run, IAM, Secrets
│       └── variables.tf             # Biến cấu hình Terraform
├── it_helpdesk_agent/
│   ├── agent.py                     # Cấu hình Multi-Agent 3 cấp bậc (L1, L2, L3)
│   ├── fast_api_app.py              # Ứng dụng FastAPI, Middleware và định tuyến
│   ├── app_utils/
│   │   ├── env.py                   # Quản lý nạp biến môi trường & Secret Manager
│   │   └── sso_auth.py              # Xác thực OIDC JWKS, RBAC ContextVar & Middleware
│   └── tools/
│       ├── compliance_tool.py       # Công cụ phân tích SLA & hợp đồng IT (RBAC)
│       ├── log_analyzer.py          # Công cụ phân tích log lỗi Root Cause Analysis (RBAC)
│       ├── mcp_config.py            # Cấu hình Toolset Enterprise RAG MCP
│       ├── ticketing_tool.py        # Quản lý Ticket (Hỗ trợ Firestore + fallback cache)
│       └── enterprise_rag_mcp/      # Máy chủ Model Context Protocol (MCP) nội bộ
│           ├── knowledge_store.py   # Mock cơ sở tri thức hệ thống ERP/HRM/CRM
│           ├── main.py              # Server MCP FastMCP
│           └── rag_models.py        # Schemas dữ liệu RAG
└── tests/
    └── unit/                        # Bộ kiểm thử tự động (37 test cases)
        ├── test_agent_hierarchy.py  # Test cấu trúc phân cấp agent và model
        ├── test_compliance_tool.py  # Test trích xuất SLA 2 chiều & RBAC
        ├── test_enterprise_rag.py   # Test MCP tra cứu tri thức
        ├── test_env.py              # Test nạp secret & env
        ├── test_log_analyzer.py     # Test nhận diện lỗi OOM, DB, Disk, Null & RBAC
        ├── test_sso_auth.py         # Test OIDC JWKS, Fail-closed domain, RBAC, Middleware
        └── test_ticketing_tool.py   # Test tạo, cập nhật, chuyển tiếp ticket
```
