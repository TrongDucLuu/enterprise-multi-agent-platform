# HƯỚNG DẪN VẬN HÀNH & TRIỂN KHAI HẠ TẦNG (DEVOPS RUNBOOK & OPERATIONAL GUIDE)
## Hệ thống Enterprise Multi-Agent AI Platform (Google Cloud Run v2, BigQuery Vector Search, ADK & SSO)

| Thuộc tính | Giá trị | Thuộc tính | Giá trị |
|---|---|---|---|
| **Hệ thống** | Enterprise Multi-Agent AI Platform | **Phiên bản tài liệu** | `2.2.0-Enterprise` |
| **Môi trường mục tiêu** | Production / Staging / Development | **Chủ quản kỹ thuật** | DevOps & Cloud Platform |
| **Mô hình AI** | Gemini 2.5 / Gemini 1.5 Pro & Flash | **Phê duyệt bởi** | Principal Cloud Architect |
| **Hạ tầng Cloud** | Google Cloud Platform (GCP) | **Ngày cập nhật** | 02/09/2026 |

---

## 1. Tổng Quan Kiến Trúc Hạ Tầng & Thành Phần Vận Hành

Hệ thống **Enterprise Multi-Agent AI Platform** được thiết kế theo kiến trúc phi trạng thái (**Stateless Microservices**), đóng gói container chuẩn OCI và triển khai trên hạ tầng **Google Cloud Run thế hệ 2 (Cloud Run v2)**. Hệ thống phân tách rõ ràng giữa Core Engine và Domain Packs, phối hợp chặt chẽ giữa các dịch vụ PaaS và Serverless cao cấp của Google Cloud Platform nhằm đảm bảo tối ưu hóa độ trễ, khả năng tự động mở rộng (Autoscaling), độ tin cậy và tối thiểu hóa chi phí vận hành cố định.

### Danh mục các thành phần hạ tầng cốt lõi:

| Thành phần GCP | Dịch vụ đảm nhiệm | Mô tả vai trò kỹ thuật & Cơ chế vận hành |
|---|---|---|
| **Compute Layer** | **Google Cloud Run (v2)** | Chạy ứng dụng FastAPI & ADK Agents. Cấu hình 2 vCPU, 2GB RAM, concurrency 80, tự động scale 0 -> N instances với `min_instances = 1` trên Production. |
| **Vector Knowledge Base** | **BigQuery Vector Search** | Lưu trữ tài liệu RAG & vector embeddings (Dataset: `it_helpdesk_kb`). Serverless 100%, cosine vector indexing, chi phí cố định 0 USD. |
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
| `DOMAIN_PACK` | **Có** | `it-helpdesk` | Không | Gói nghiệp vụ kích hoạt: `it-helpdesk`, `_template`, hoặc custom domain pack. |
| `GOOGLE_CLOUD_PROJECT` | **Có** | — | Không | ID của dự án Google Cloud Platform (GCP Project ID). |
| `GOOGLE_CLOUD_REGION` | **Có** | `us-central1` | Không | Vùng triển khai Cloud Run và BigQuery (vd: `us-central1`, `asia-southeast1`). |
| `SSO_CLIENT_ID` | **Có** | — | Có | OAuth 2.0 Client ID được cấp từ GCP Identity Console. |
| `ALLOWED_DOMAINS` | **Có (Prod)** | — | Không | Danh sách domain email công ty được phép (vd: `company.com,corp.com`). Bắt buộc trong Prod để kích hoạt **Fail-Closed**. |
| `ALLOW_LOCAL_DEV_SSO` | Không | `false` | Không | Bật tính năng giả lập SSO cho local dev. Tự động bị khóa thành `false` trên môi trường Production. |
| `SSO_JWT_SECRET` | Không | — | Có | Khóa bí mật HS256 chỉ dùng để ký token thử nghiệm trong local dev. |
| `USE_FIRESTORE_TICKETS`| Không | `false` | Không | Bật lưu trữ Firestore cho tickets/cases (bắt buộc bật trên Cloud Run Production). |
| `KNOWLEDGE_BACKEND` | Không | `in_memory` | Không | Backend cho RAG Knowledge Store: `'in_memory'` hoặc `'bigquery'`. |
| `BIGQUERY_KB_DATASET` | Không | `it_helpdesk_kb` | Không | Dataset BigQuery chứa bảng vectors và tài liệu tri thức. |
| `REDIS_HOST` | Không | — | Không | Địa chỉ IP nội bộ của Google Cloud Memorystore Redis instance. |
| `REDIS_PORT` | Không | `6379` | Không | Cổng kết nối Redis (mặc định: `6379`). |
| `REDIS_AUTH_STRING` | Không | — | Có | Chuỗi mật khẩu xác thực Redis Memorystore AUTH từ Secret Manager. |
| `REDIS_CA_CERT` | Không | — | Có | Chứng chỉ Server CA cho Redis TLS In-Transit Encryption từ Secret Manager. |
| `SEMANTIC_CACHE_ENABLED` | Không | `true` | Không | Bật/Tắt lớp bộ đệm ngữ nghĩa cho câu hỏi lặp lại. |
| `SEMANTIC_CACHE_THRESHOLD` | Không | `0.92` | Không | Ngưỡng độ tương đồng Cosine Similarity để coi là trùng khớp. |
| `OTEL_TO_CLOUD` | Không | `false` | Không | Đẩy OpenTelemetry traces lên GCP Cloud Trace. |

> **WARNING — Quy định An toàn Thông tin — Cơ chế Fail-Closed**:  
> Trên môi trường Production (`ENVIRONMENT=production`), nếu bất kỳ biến bảo mật nào (`ALLOWED_DOMAINS`, `SSO_CLIENT_ID`, `USE_FIRESTORE_TICKETS`) bị thiếu hoặc cấu hình sai, hệ thống sẽ **ngay lập tức dừng khởi động (Fail-Closed)** và từ chối toàn bộ request thay vì fallback ngầm sang chế độ không an toàn.

---

## 3. Hướng Dẫn Khởi Tạo Hạ Tầng Tự Động (Terraform IaC)

Toàn bộ tài nguyên đám mây được định nghĩa dưới dạng mã (**Infrastructure as Code**) trong thư mục `deployment/terraform/`. Bộ mã Terraform đã được cấu hình tuân thủ nguyên tắc đặc quyền tối thiểu (Least Privilege) và quản lý vòng đời tài nguyên thông minh.

### 3.1. Cấu trúc Tài nguyên Terraform (Terraform Resource Mapping)

- `google_service_account.agent_sa`: Service Account đại diện cho ứng dụng chạy trên Cloud Run.
- `google_project_iam_member`: Cấp các quyền chặt chẽ:
  - `roles/aiplatform.user` (Vertex AI)
  - `roles/bigquery.dataEditor` & `roles/bigquery.jobUser` (BigQuery)
  - `roles/logging.logWriter` (Cloud Logging)
  - `roles/secretmanager.secretAccessor` (Secret Manager)
  - `roles/datastore.user` (Cloud Firestore)
- `google_bigquery_dataset.kb_dataset`: Khởi tạo dataset `it_helpdesk_kb` lưu trữ vector articles.
- `google_redis_instance.cache_redis`: Khởi tạo Memorystore Redis với `auth_enabled = true` và `transit_encryption_mode = "SERVER_AUTHENTICATION"`.
- `google_secret_manager_secret`: Quản lý `redis_auth` và `redis_ca_cert`.
- `google_cloud_run_v2_service.agent_service`: Định nghĩa Cloud Run Service với cấu hình CPU, Memory, VPC Access Connector, và các khối `lifecycle { precondition }` nghiêm ngặt.

### 3.2. Quy trình Thực thi Terraform

```bash
# Bước 1: Di chuyển vào thư mục Terraform
cd deployment/terraform

# Bước 2: Khởi tạo Terraform Provider & State Backend
terraform init

# Bước 3: Kiểm tra kế hoạch thay đổi (Dry-Run)
terraform plan \
  -var="project_id=my-company-it-prod" \
  -var="region=us-central1" \
  -var="domain_pack=it-helpdesk" \
  -var="sso_client_id=123456789-abc.apps.googleusercontent.com" \
  -var="allowed_domains=mycompany.com,corp.mycompany.com" \
  -var="environment=production" \
  -var="redis_enabled=true" \
  -out=tfplan.binary

# Bước 4: Áp dụng thay đổi vào hạ tầng GCP
terraform apply tfplan.binary
```

---

## 4. Quy Trình Đóng Gói Container & Triển Khai (CI/CD Pipeline)

Hệ thống sử dụng Dockerfile chuẩn Production với chiến lược Multi-Stage Build và quản lý package cực nhanh qua công cụ UV của Astral. Container được chạy dưới quyền người dùng không đặc quyền (non-root user `appuser` với UID 1001) để phòng chống leo thang đặc quyền.

### 4.1. Quy trình Build & Push Container Image

```bash
# Đặt biến môi trường dự án
export PROJECT_ID="my-company-it-prod"
export REGION="us-central1"
export REPO="it-helpdesk-repo"
export IMAGE_TAG="$(git rev-parse --short HEAD)"
export IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/enterprise-agent:${IMAGE_TAG}"

# Build và push image lên Artifact Registry qua Cloud Build
gcloud builds submit --tag ${IMAGE_URI} .
```

### 4.2. Cập nhật Revision Mới trên Cloud Run

```bash
# Cập nhật container image cho Cloud Run service
gcloud run services update enterprise-agent \
  --image=${IMAGE_URI} \
  --region=${REGION} \
  --project=${PROJECT_ID}
```

### 4.3. Chiến lược Di chuyển Lưu lượng & Rollback (Traffic Migration)

Cloud Run tự động kích hoạt Revision mới với 100% traffic sau khi vượt qua Healthcheck. Nếu phát hiện lỗi nghiêm trọng, thực hiện rollback về Revision trước đó chỉ trong vòng dưới 30 giây:

```bash
# Liệt kê các revision gần nhất
gcloud run revisions list --service=enterprise-agent --region=${REGION}

# Điều hướng 100% lưu lượng về Revision ổn định trước đó (vd: enterprise-agent-00042-xyz)
gcloud run services update-traffic enterprise-agent \
  --to-revisions=enterprise-agent-00042-xyz=100 \
  --region=${REGION}
```

---

## 5. Quy Trình Vận Hành Chuẩn (Standard Operating Procedures)

### SOP-01: Đồng bộ Môi trường & Dependencies Cục bộ
```bash
uv sync
```

### SOP-02: Khởi chạy Chế độ Kiểm thử Tương tác CLI
```bash
uv run python main.py --mode cli
```

### SOP-03: Khởi chạy Web Server FastAPI & ADK UI
```bash
uv run python main.py --mode serve --host 0.0.0.0 --port 8080
```

### SOP-04: Thực thi Toàn bộ Bộ Kiểm thử Tự động (3-Suite CI Protocol)
```bash
# Suite 1: Môi trường Development với Local SSO
ENVIRONMENT=development ALLOW_LOCAL_DEV_SSO=true .venv/bin/pytest tests/ -q

# Suite 2: Môi trường Production không cho phép Local SSO
ENVIRONMENT=production ALLOW_LOCAL_DEV_SSO=false .venv/bin/pytest tests/ -q

# Suite 3: Cô lập Domain Pack Template
DOMAIN_PACK=_template ENVIRONMENT=development .venv/bin/pytest tests/ -q
```

### SOP-05: Quản trị & Giám sát Semantic Cache
```bash
# Lấy thống kê hiệu năng bộ đệm
curl -X GET http://localhost:8080/api/cache/stats \
  -H "Authorization: Bearer <VALID_OIDC_TOKEN>"

# Tra cứu trực tiếp câu hỏi tương tự
curl -X GET "http://localhost:8080/api/cache/query?q=cách+đổi+mật+khẩu+wifi&threshold=0.92" \
  -H "Authorization: Bearer <VALID_OIDC_TOKEN>"
```

### SOP-06: Quy trình Onboarding Khách Hàng Mới (Domain Pack Deployment)
1. Tạo domain pack mới trong `domain_packs/<tenant_name>/` hoặc cấu hình `domain_packs/it-helpdesk/`.
2. Khai báo hệ thống nghiệp vụ trong `domain_packs/<tenant_name>/systems.yaml` và case schema trong `case_schema.yaml`.
3. Đặt tài liệu kỹ thuật/SOP vào `data/knowledge_base/` hoặc `domain_packs/<tenant_name>/knowledge.yaml`.
4. Chạy kiểm thử mô phỏng (Dry-Run):
   ```bash
   python scripts/ingest_knowledge_base.py --dry-run
   ```
5. Thực hiện nạp chính thức dữ liệu và sinh vector embedding 768-dim vào BigQuery:
   ```bash
   python scripts/ingest_knowledge_base.py
   ```
6. Xác minh truy vấn ngữ nghĩa:
   ```bash
   python scripts/ingest_knowledge_base.py --test-query "lỗi phân quyền SAP PO"
   ```

---

## 6. Giám Sát, Cảnh Báo & Khả Năng Quan Sát (Observability & Monitoring)

### 6.1. Healthcheck & Liveness/Readiness Probes
Endpoint `/healthz` hoạt động công khai không cần auth, phục vụ kiểm tra sức khỏe của Cloud Run Load Balancer:
- Trả về `{"status": "ok", "service": "enterprise_agent"}` với mã HTTP 200 OK.

### 6.2. Ma trận Cảnh báo Vận hành (Alerting Policy Matrix)

| Chỉ số Giám sát | Ngưỡng Cảnh báo | Mức độ Nghiêm trọng | Hành động Vận hành Khuyến nghị |
|---|---|---|---|
| **HTTP 5xx Error Rate** | > 1.0% trong 5 phút | **CRITICAL (P1)** | Kiểm tra Cloud Logging tìm Exception. Rollback revision nếu xảy ra sau khi deploy. |
| **P95 Latency** | > 3.0 giây trong 5 phút | **HIGH (P2)** | Kiểm tra tỉ lệ Cache Hit Rate, độ trễ API Vertex AI hoặc tắc nghẽn BigQuery. |
| **OIDC Auth Failures** | > 50 lỗi / phút | **HIGH (P2)** | Kiểm tra cấu hình `ALLOWED_DOMAINS`, JWKS Google certs hoặc token Client ID. |
| **Container Memory Usage** | > 85% giới hạn (1.7GB) | **MEDIUM (P3)** | Kiểm tra rò rỉ bộ nhớ (Memory Leak), điều chỉnh giới hạn RAM lên 4GB. |
| **BigQuery Quota Error** | > 5 lỗi QuotaExceeded | **MEDIUM (P3)** | Yêu cầu tăng quota BigQuery Query Execution trên GCP Console. |

---

## 7. Kịch Bản Xử Lý Sự Cố & Khôi Phục Thảm Họa (Incident Response)

### Sự cố 1: Lỗi Xác thực SSO Hàng loạt (HTTP 401/403)
- **Triệu chứng**: Người dùng không thể đăng nhập hoặc nhận thông báo `'Truy cập bị từ chối: Domain không được phép'`.
- **Nguyên nhân gốc rễ**: Biến `ALLOWED_DOMAINS` bị thiếu domain mới sáp nhập, hoặc `SSO_CLIENT_ID` không khớp với Google Client ID trên Frontend.
- **Quy trình xử lý**:
  1. Kiểm tra log Cloud Logging: `resource.type="cloud_run_revision" textPayload=~"OIDC"`.
  2. Cập nhật biến môi trường trên Cloud Run:
     ```bash
     gcloud run services update enterprise-agent --set-env-vars="ALLOWED_DOMAINS=company.com,newdomain.com"
     ```

### Sự cố 2: Lỗi Kết nối Cloud Firestore (Production Fail-Closed)
- **Triệu chứng**: Log xuất hiện `'Firestore unavailable... Failing-closed in production'`.
- **Tác động**: Hệ thống từ chối ghi nhận case mới để bảo vệ tính toàn vẹn dữ liệu (không lưu split-brain memory trong Production).
- **Quy trình xử lý**:
  1. Kiểm tra quyền IAM của Service Account: đảm bảo có quyền `roles/datastore.user`.
  2. Kiểm tra Firestore Database đã được kích hoạt trên GCP Project hay chưa.

### Sự cố 3: Vượt Hạn Ngạch Vertex AI (Rate Limit 429)
- **Triệu chứng**: Log xuất hiện mã lỗi `RESOURCE_EXHAUSTED` hoặc `429 Too Many Requests`.
- **Cơ chế tự phục hồi**: Mã nguồn đã tích hợp `HttpRetryOptions(attempts=3)` với Exponential Backoff.
- **Quy trình xử lý khẩn cấp**: Tăng hạn ngạch Quota `'GenerateContent requests per minute'` trên Google Cloud Console Quotas & Limits.

---

## 8. Danh Mục Kiểm Tra Bảo Mật & Bàn Giao (Go-Live Checklist)

| Hạng mục Kiểm tra | Trạng thái | Yêu cầu Tiêu chuẩn Bắt buộc |
|---|---|---|
| **HTTPS / SSL Termination** | **BẮT BUỘC** | Cloud Run tự động kích hoạt chứng chỉ SSL/TLS được quản lý bởi Google. |
| **Google OIDC JWKS Verification** | **BẮT BUỘC** | Đảm bảo verify chữ ký RS256 trực tiếp từ `accounts.google.com`. |
| **Fail-Closed Domain Whitelist** | **BẮT BUỘC** | Biến `ALLOWED_DOMAINS` đã cấu hình chính xác danh sách domain nội bộ. |
| **Dev Mock Token Disabled** | **BẮT BUỘC** | Biến `ALLOW_LOCAL_DEV_SSO` phải bằng `'false'` trong môi trường Production. |
| **Secrets in Secret Manager** | **BẮT BUỘC** | Tuyệt đối không lưu API keys hoặc secrets dưới dạng Plaintext trong mã nguồn. |
| **Redis Memorystore Auth & TLS** | **BẮT BUỘC** | Bật xác thực Auth String và chứng chỉ Server CA trong Secret Manager. |
| **Service Account Least Privilege** | **BẮT BUỘC** | Chỉ cấp các IAM roles tối thiểu cần thiết (Vertex, BigQuery, Firestore, Logging, Secrets). |
| **Unit Tests 100% Pass** | **BẮT BUỘC** | Bộ 361 unit test cases phải vượt qua 100% trên cả 3 môi trường trước khi bàn giao. |\n