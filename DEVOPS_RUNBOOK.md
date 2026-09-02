# HƯỚNG DẪN VẬN HÀNH & TRIỂN KHAI HẠ TẦNG (DEVOPS RUNBOOK & OPERATIONAL GUIDE)
## Nền Tảng Enterprise Multi-Agent AI Platform (Google Cloud Run v2, BigQuery Vector Search, ADK & SSO)

| Thuộc tính | Giá trị | Thuộc tính | Giá trị |
|---|---|---|---|
| **Hệ thống** | Enterprise Multi-Agent AI Platform (`agent_core`) | **Phiên bản tài liệu** | `2.2.0-Enterprise` |
| **Môi trường mục tiêu** | Production / Staging / Development | **Chủ quản kỹ thuật** | DevOps & Cloud Platform |
| **Mô hình AI** | Gemini 2.5 / Gemini 1.5 Pro & Flash | **Phê duyệt bởi** | Principal Cloud Architect |
| **Hạ tầng Cloud** | Google Cloud Platform (GCP) | **Ngày cập nhật** | 02/09/2026 |

---

## 1. Tổng Quan Kiến Trúc Hạ Tầng & Thành Phần Vận Hành

Hệ thống **Enterprise Multi-Agent AI Platform** được thiết kế theo kiến trúc phi trạng thái (**Stateless Microservices**), đóng gói container chuẩn OCI và triển khai trên hạ tầng **Google Cloud Run thế hệ 2 (Cloud Run v2)**. Hệ thống phân tách rõ ràng giữa Core Platform Engine (`agent_core/`) và các Gói Nghiệp Vụ (`domain_packs/`), phối hợp chặt chẽ giữa các dịch vụ PaaS và Serverless cao cấp của Google Cloud Platform nhằm đảm bảo tối ưu hóa độ trễ, khả năng tự động mở rộng (Autoscaling), độ tin cậy và tối thiểu hóa chi phí vận hành cố định.

### Danh mục các thành phần hạ tầng cốt lõi:

| Thành phần GCP | Dịch vụ đảm nhiệm | Mô tả vai trò kỹ thuật & Cơ chế vận hành |
|---|---|---|
| **Compute Layer** | **Google Cloud Run (v2)** | Chạy ứng dụng FastAPI & ADK Agents. Cấu hình 2 vCPU, 2GB RAM, concurrency 80, tự động scale 0 -> N instances với `min_instances = 1` trên Production. |
| **Vector Knowledge Base** | **BigQuery Vector Search** | Lưu trữ tài liệu RAG & vector embeddings (Dataset cấu hình qua `BIGQUERY_KB_DATASET`). Serverless 100%, cosine vector indexing, chi phí cố định 0 USD khi rảnh rỗi. |
| **Case & Ticket State Store** | **Cloud Firestore** | Lưu trữ trạng thái Case/Ticket với Optimistic Concurrency Control (OCC), versioning và nhật ký kiểm toán bất biến (`history`). Chế độ Production thực thi nghiêm ngặt **Fail-Closed**. |
| **Identity & SSO** | **Google OIDC + Cloud IAM** | Xác thực danh tính qua JWKS public keys của `accounts.google.com`, phân quyền theo domain email (`ALLOWED_DOMAINS`) và vai trò RBAC (`SSOUser`). |
| **Distributed Semantic Cache** | **Redis Memorystore / In-Memory** | Bộ đệm ngữ nghĩa câu hỏi phân vùng theo cấp độ bảo mật (`_c0..c3_`), mã hóa TLS In-Transit, xác thực mật khẩu qua Secret Manager. |
| **Secret Management** | **GCP Secret Manager** | Lưu trữ an toàn các biến môi trường nhạy cảm, API keys, Redis Auth String và Redis Server CA Cert. |
| **Container Registry** | **Artifact Registry** | Kho lưu trữ Docker images bảo mật (Format: Docker v2, Region: `us-central1` hoặc `asia-southeast1`). |
| **Observability** | **Cloud Trace & Logging** | Thu thập structured logs chuẩn JSON, tracing phân tán qua OpenTelemetry và giám sát sức khỏe dịch vụ. |

> **TIP — Tối ưu hóa Chi phí Hạ tầng Serverless**:  
> Nhờ áp dụng BigQuery Vector Search thay thế cho Vertex AI Vector Search Index Endpoints chuyên dụng, chi phí hạ tầng tĩnh giảm từ ~300 USD/tháng xuống gần 0 USD/tháng cho các doanh nghiệp có kho tri thức dưới 100.000 vectors.

---

## 2. Bảng Quản Lý Biến Môi Trường & Bí Mật Bảo Mật

Mọi tham số cấu hình của hệ thống được quản lý thông qua biến môi trường (**Environment Variables**) và **Google Secret Manager**. Khi triển khai trên Production, toàn bộ các biến bảo mật phải được nạp thông qua cơ chế Secret Reference của Cloud Run thay vì gán giá trị Plaintext.

| Tên Biến Môi Trường | Bắt buộc | Giá trị mặc định | Secret Manager | Mục đích & Hướng dẫn Cấu hình |
|---|---|---|---|---|
| `ENVIRONMENT` | **Có** | `development` | Không | Môi trường thực thi: `'development'`, `'staging'`, `'production'`. |
| `DOMAIN_PACK` | **Có** | `it-helpdesk` | Không | Gói nghiệp vụ kích hoạt: `it-helpdesk`, `_template`, `hr-service`, `legal-compliance`... |
| `GOOGLE_CLOUD_PROJECT` | **Có** | — | Không | ID của dự án Google Cloud Platform (GCP Project ID). |
| `GOOGLE_CLOUD_REGION` | **Có** | `us-central1` | Không | Vùng triển khai Cloud Run và BigQuery (vd: `us-central1`, `asia-southeast1`). |
| `SSO_CLIENT_ID` | **Có** | — | Có | OAuth 2.0 Client ID được cấp từ GCP Identity Console. |
| `ALLOWED_DOMAINS` | **Có (Prod)** | — | Không | Danh sách domain email công ty được phép (vd: `company.com,corp.com`). Bắt buộc trong Prod để kích hoạt **Fail-Closed**. |
| `ALLOW_LOCAL_DEV_SSO` | Không | `false` | Không | Bật tính năng giả lập SSO cho local dev. Tự động bị khóa thành `false` trên môi trường Production. |
| `SSO_JWT_SECRET` | Không | — | Có | Khóa bí mật HS256 chỉ dùng để ký token thử nghiệm trong local dev. |
| `USE_FIRESTORE_TICKETS`| Không | `false` | Không | Bật lưu trữ Firestore cho tickets/cases (bắt buộc bật trên Cloud Run Production). |
| `CASE_COLLECTION` | Không | `cases` | Không | Tên Firestore collection lưu trữ cases (vd: `cases`, `tickets`, `hr_requests`). |
| `KNOWLEDGE_BACKEND` | Không | `in_memory` | Không | Backend cho RAG Knowledge Store: `'in_memory'` hoặc `'bigquery'`. |
| `BIGQUERY_KB_DATASET` | Không | `enterprise_kb` | Không | Dataset BigQuery chứa bảng vectors và tài liệu tri thức cho domain active. |
| `REDIS_HOST` | Không | — | Không | Địa chỉ IP nội bộ của Google Cloud Memorystore Redis instance. |
| `REDIS_PORT` | Không | `6379` | Không | Cổng kết nối Redis (mặc định: `6379`). |
| `REDIS_AUTH_STRING` | Không | — | Có | Chuỗi mật khẩu xác thực Redis Memorystore AUTH từ Secret Manager. |
| `REDIS_CA_CERT` | Không | — | Có | Chứng chỉ Server CA cho Redis TLS In-Transit Encryption từ Secret Manager. |
| `SEMANTIC_CACHE_ENABLED` | Không | `true` | Không | Bật/Tắt lớp bộ đệm ngữ nghĩa cho câu hỏi lặp lại. |
| `SEMANTIC_CACHE_THRESHOLD` | Không | `0.92` | Không | Ngưỡng độ tương đồng Cosine Similarity để coi là trùng khớp. |
| `OTEL_TO_CLOUD` | Không | `false` | Không | Đẩy OpenTelemetry traces lên GCP Cloud Trace. |

> **WARNING — Quy định An toàn Thông tin — Cơ chế Fail-Closed**:  
> Trên môi trường Production (`ENVIRONMENT=production` hoặc biến `K_SERVICE` tồn tại), nếu thiếu bất kỳ biến nào trong nhóm (`ALLOWED_DOMAINS`, `SSO_CLIENT_ID`, `USE_FIRESTORE_TICKETS`), hệ thống sẽ từ chối khởi động ngay lập tức (Fail-Closed) để ngăn ngừa rủi ro bảo mật.

---

## 3. Quản Lý Hạ Tầng Bằng Mã Nguồn (Terraform IaC)

Toàn bộ hạ tầng GCP được định nghĩa bằng Terraform trong thư mục `deployment/terraform/`:

```
deployment/terraform/
├── main.tf           # Cloud Run Service v2, Artifact Registry, BigQuery Datasets & IAM
├── variables.tf      # Khai báo biến đầu vào & ràng buộc kiểm tra
├── outputs.tf        # Đầu ra thông tin hạ tầng (Service URL, Service Account Email...)
├── redis.tf          # Dedicated VPC, Serverless VPC Access Connector, Memorystore Redis
└── terraform.tfvars  # Giá trị biến môi trường cụ thể cho từng GCP Project
```

### Các Khối Kiểm Soát Nghiêm Ngặt (Terraform Preconditions):
- **Kiểm tra Domain Whitelist**: Ngăn chặn `allowed_domains` bị rỗng hoặc chứa ký tự đại diện `*` trên môi trường Production.
- **Kiểm tra Instance Sẵn sàng**: Yêu cầu `min_instance_count >= 1` trên Production để loại bỏ hiện tượng Cold-Start.
- **Kiểm tra RAG Backend**: Bắt buộc `knowledge_backend == "bigquery"` trên Production, từ chối triển khai nếu dùng `in_memory`.
- **Deduplication VPC Connector**: Sử dụng một khối `vpc_access` duy nhất trong Cloud Run resource, loại bỏ hoàn toàn cảnh báo trùng lặp cấu hình.

---

## 4. Quy Trình Kiểm Thử & Xác Thực CI/CD (3-Suite CI Protocol)

Trước khi tiến hành build Docker image hoặc deploy lên môi trường Staging/Production, bắt buộc phải chạy thành công 100% cả 3 bộ test suite với tổng cộng **361 test cases**:

```bash
# ==============================================================================
# BỘ KIỂM THỬ 1: MÔI TRƯỜNG PHÁT TRIỂN (LOCAL DEV SSO)
# ==============================================================================
ENVIRONMENT=development ALLOW_LOCAL_DEV_SSO=true .venv/bin/pytest tests/ -q
# Kết quả yêu cầu: 361 passed (100% Pass)

# ==============================================================================
# BỘ KIỂM THỬ 2: MÔI TRƯỜNG PRODUCTION (FAIL-CLOSED SECURITY ENFORCEMENT)
# ==============================================================================
ENVIRONMENT=production ALLOW_LOCAL_DEV_SSO=false .venv/bin/pytest tests/ -q
# Kết quả yêu cầu: 361 passed (100% Pass)

# ==============================================================================
# BỘ KIỂM THỬ 3: CÔ LẬP DOMAIN PACK TEMPLATE
# ==============================================================================
DOMAIN_PACK=_template ENVIRONMENT=development .venv/bin/pytest tests/ -q
# Kết quả yêu cầu: 361 passed (100% Pass)
```

---

## 5. Quy Trình Vận Hành Chuẩn (Standard Operating Procedures - SOPs)

### SOP-01: Triển khai Bản Phát Hành Mới (Zero-Downtime Deployment)
1. **Kiểm thử cục bộ**: Chạy trọn vẹn 3 bộ test suites (361 test cases).
2. **Đóng gói Docker Image**:
   ```bash
   gcloud builds submit --tag gcr.io/${PROJECT_ID}/enterprise-multi-agent-platform:v2.2.0
   ```
3. **Cập nhật Cloud Run Revision**:
   ```bash
   gcloud run deploy enterprise-multi-agent-platform \
       --image gcr.io/${PROJECT_ID}/enterprise-multi-agent-platform:v2.2.0 \
       --region ${REGION}
   ```
4. **Kiểm tra Liveness & Readiness**:
   ```bash
   curl -f https://${SERVICE_URL}/healthz
   ```

### SOP-02: Quy trình Onboarding Khách hàng / Domain Mới
1. **Khởi tạo Domain Pack**: Tạo thư mục `domain_packs/<new-domain>/` từ mẫu `domain_packs/_template/`.
2. **Định nghĩa Agent & Schema**: Cập nhật `pack.yaml`, `agents.yaml`, `case_schema.yaml`, `systems.yaml`.
3. **Nạp Kho Tri Thức Vector**: Tải tài liệu nghiệp vụ vào BigQuery dataset của tenant.
4. **Triển khai Hạ tầng Độc lập**: Chạy Terraform stack cho GCP Project của khách hàng với `DOMAIN_PACK=<new-domain>`.

---

## 6. Xử Lý Sự Cố Khẩn Cấp (Incident Response Playbooks)

| Sự cố | Triệu chứng | Nguyên nhân Khả dĩ | Hành động Xử lý Khắc phục |
|---|---|---|---|
| **Lỗi 401 Unauthorized Hàng Loạt** | Mọi API trả về `401 Invalid Token` | Token OIDC hết hạn, domain email bị chặn, hoặc sai `SSO_CLIENT_ID`. | Kiểm tra log Cloud Run, xác minh `ALLOWED_DOMAINS` trong biến môi trường và Google IAM JWKS. |
| **Lỗi 500 Case Concurrency Conflict** | Lỗi cập nhật Case/Ticket với mã `CaseConcurrencyConflictError` | Hai kỹ sư cùng cập nhật một case đồng thời. | Client thực hiện đọc lại version mới nhất (`get_case`) và retry cập nhật với `expected_version` mới. |
| **Lỗi RAG Fallback Kích Hoạt** | Log ghi nhận `Warning: Cross-Encoder model unavailable, falling back to Cosine Distance` | Model weights Re-ranker không tải được hoặc thiếu RAM. | Hệ thống tự duy trì hoạt động qua Fallback. Cần kiểm tra RAM của Cloud Run instance (khuyến nghị nâng lên 2GB-4GB). |
