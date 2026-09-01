# Sổ Tay Onboarding Khách Hàng & Vận Hành Nền Tảng AI Agent

> **Dành cho:** Solutions Architects, DevOps/SRE Engineers, và AI Delivery Leads  
> **Nền tảng:** Enterprise Autonomous Agent Platform (`agent_core`)  
> **Phiên bản:** 2.0.0 (Decoupled Domain Pack & Isolated GCP Project Architecture)

---

## 1. Mô Hình Triển Khai & Chiến Lược Độc Lập Hạ Tầng (Infrastructure Isolation)

Hệ thống được thiết kế theo mô hình **Độc Lập Hạ Tầng (1 Khách Hàng = 1 GCP Project Riêng Biệt)**:
- **Tuyệt đối không dùng chung Database (No Shared Multi-Tenancy DB):** Dữ liệu của mỗi khách hàng được bảo vệ hoàn toàn bởi biên giới IAM và hạ tầng Cloud của chính dự án đó.
- **Tách rời Platform Core & Domain Pack:** Mã nguồn `agent_core/` được giữ nguyên. Mọi nghiệp vụ, tài liệu tri thức, phân quyền và cấu hình của khách hàng được quản lý độc lập tại `domain_packs/<pack_id>/`.

```
                            MÔ HÌNH ONBOARDING KHÁCH HÀNG
                                          │
                  ┌───────────────────────┴───────────────────────┐
                  ▼                                               ▼
     [1. TẦNG NGHIỆP VỤ (DOMAIN PACK)]                [2. TẦNG HẠ TẦNG (GCP PROJECT)]
     - Tạo folder domain_packs/<client-id>/           - Provision GCP Project riêng biệt
     - Khai báo pack.yaml, agents.yaml                - Deploy BigQuery, Firestore, Redis
     - Định nghĩa case_schema.yaml, systems.yaml      - Deploy Cloud Run (chạy agent_core)
     - Nạp tri thức, Facts, Obligations               - Cấu hình SSO OIDC & Domain Whitelist
```

---

## 2. Quy Trình Onboarding Khách Hàng Mới (7 Bước Chuẩn)

### Bước 1: Khởi Tạo Domain Pack Từ Template
Sao chép thư mục khung mẫu từ `domain_packs/_template/` sang thư mục mới:

```bash
cp -r domain_packs/_template domain_packs/<client_domain_name>
```

Cấu trúc thư mục của khách hàng gồm:
```
domain_packs/<client_domain_name>/
├── pack.yaml          # Định danh, phiên bản, entry_agent
├── agents.yaml        # Danh sách agent, model, instructions, tools được phép
├── case_schema.yaml   # Phân loại sự cố, mức độ ưu tiên, trạng thái
├── systems.yaml       # Hệ thống nội bộ, vai trò truy cập (admin_roles, user_role_mappings)
├── eval_set.jsonl     # 10-20 câu hỏi test thực tế kèm expected_agent, expected_tool
└── README.md          # Tài liệu nghiệp vụ dành cho khách hàng
```

---

### Bước 2: Cấu Hình `pack.yaml` & `agents.yaml`
1. **`pack.yaml`**: Khai báo ID, phiên bản và agent khởi tạo (`entry_agent`):
   ```yaml
   id: "<client-pack-id>"
   name: "Customer Service & Operations Agent"
   version: "1.0.0"
   min_core_version: "2.0.0"
   entry_agent: "root_orchestrator"
   ```

2. **`agents.yaml`**: Định nghĩa cây Agent và danh sách công cụ được phép:
   ```yaml
   agents:
     root_orchestrator:
       name: "Root Orchestrator"
       model: "gemini-2.5-flash"
       instruction: "Bạn là trợ lý điều phối trung tâm. Phân luồng câu hỏi chính xác tới các agent chuyên môn..."
       sub_agents:
         - "l1_agent"
         - "l2_agent"
       tools:
         - "lookup_fact"

     l1_agent:
       name: "L1 Support Agent"
       model: "gemini-2.5-flash"
       instruction: "Hỗ trợ các vấn đề thường gặp và tra cứu thông tin cơ bản..."
       tools:
         - "lookup_fact"
         - "create_case"
         - "get_case"
         - "list_user_cases"

     l2_agent:
       name: "L2 Knowledge Specialist"
       model: "gemini-2.5-flash"
       instruction: "Tra cứu tài liệu quy trình chuyên sâu trong kho tri thức doanh nghiệp..."
       tools:
         - "search_enterprise_knowledge"
         - "get_system_manual"
   ```

> [!IMPORTANT]
> - Chỉ dẫn bảo mật `INDIRECT_PROMPT_INJECTION_DEFENSE_INSTRUCTION` sẽ được hệ thống `agent_builder.py` tự động tiêm vào cuối instructions của tất cả các Agent, bạn không cần phải copy paste thủ công.

---

### Bước 3: Định Nghĩa `case_schema.yaml` & `systems.yaml`
1. **`case_schema.yaml`**: Tùy biến các danh mục sự cố phù hợp với nghiệp vụ của khách hàng:
   ```yaml
   categories:
     - "ACCOUNT_ACCESS"
     - "HARDWARE_REQUEST"
     - "POLICY_INQUIRY"
   priorities:
     - "LOW"
     - "MEDIUM"
     - "HIGH"
     - "CRITICAL"
   statuses:
     - "OPEN"
     - "IN_PROGRESS"
     - "RESOLVED"
     - "CLOSED"
   ```

2. **`systems.yaml`**: Định nghĩa danh mục phần mềm, phân quyền RBAC và từ khóa nhận diện:
   ```yaml
   shared_admin_roles:
     - "admin"
     - "super_user"

   user_role_mappings:
     "admin@customer.com": ["admin"]
     "finance_lead@customer.com": ["accountant", "erp_user"]

   domain_keywords:
     ERP: ["sap", "po", "invoice", "me21n", "hóa đơn"]
     HRM: ["workday", "nghỉ phép", "bảo hiểm", "chấm công"]

   systems:
     ERP:
       display_name: "SAP S/4HANA"
       description: "Hệ thống quản lý tài chính và mua sắm"
       roles: ["erp_user", "accountant"]
     HRM:
       display_name: "Workday"
       description: "Hệ thống quản trị nhân sự và chấm công"
       roles: ["employee", "hr_specialist"]
   ```

---

### Bước 4: Chuẩn Bị & Nạp Dữ Liệu Vào Knowledge Store (Tùy Chọn RAG Backend)

Hệ thống hỗ trợ lựa chọn 1 trong 2 cơ chế RAG thông qua biến môi trường `KNOWLEDGE_BACKEND`:

#### Phương án A: BigQuery Serverless Vector Search (`KNOWLEDGE_BACKEND="bigquery"`)
- **Phù hợp với:** Khách hàng muốn tối ưu hóa chi phí hạ tầng (0 USD/tháng khi rảnh rỗi), bảo toàn 100% dữ liệu trong Data Warehouse, không egress dữ liệu ra ngoài.
- **Quy trình nạp tài liệu:**
  ```bash
  # Nạp tài liệu từ thư mục PDF/DOCX/MD của khách hàng vào BigQuery
  python scripts/ingest_knowledge_base.py \
      --project-id="customer-gcp-project-id" \
      --dataset-id="enterprise_knowledge" \
      --table-name="knowledge_articles" \
      --source-dir="/path/to/customer/documents" \
      --default-system="ERP"
  ```

#### Phương án B: Native Vertex AI Search Grounding (`KNOWLEDGE_BACKEND="vertex_ai_search"`)
- **Phù hợp với:** Khách hàng có kho tài liệu phức tạp (PDF quét scan, slide, bảng biểu định dạng đa dạng), cần tính năng Managed OCR và Semantic Extractive Segments của Google Cloud.
- **Quy trình nạp tài liệu:**
  1. Tạo **Data Store** trên Vertex AI Search & Conversation (Discovery Engine).
  2. Nạp dữ liệu từ Google Cloud Storage (`gs://customer-docs-bucket/`) vào Data Store.
  3. Cấu hình biến môi trường:
     - `VERTEX_SEARCH_DATA_STORE_ID="<customer-datastore-id>"`
     - `VERTEX_SEARCH_LOCATION="global"` (hoặc region tương ứng).

---

#### Nạp Dữ Liệu Tri Thức Cứng (L1 Facts) & Cam Kết Hợp Đồng (L3 Obligations)

1. **Bảng Tra Cứu Sự Thật Xác Định (L1 Facts):**
   - Soạn thảo danh sách Fact cứng vào bảng `enterprise_facts` trong BigQuery (IP, Port, ngưỡng SLA cứng, đầu mối liên hệ) để công cụ `lookup_fact` tra cứu không qua LLM.

2. **Sổ Đăng Ký Cam Kết Hợp Đồng & Pháp Lý (L3 Obligations):**
   - Nạp các điều khoản hợp đồng/SLA có giá trị pháp lý vào bảng `contract_obligations` trong BigQuery để công cụ `get_obligation` tra cứu có phân quyền RBAC.

---

### Bước 5: Viết và Đăng Ký Công Cụ Mở Rộng (Tùy Chọn)
Nếu khách hàng có các API hoặc cơ sở dữ liệu nội bộ đặc thù:
1. Tạo module plugin mới tại `agent_core/plugins/<plugin_name>.py`.
2. Sử dụng decorator `@register_tool("<tên_công_cụ>")` để đăng ký vào Tool Registry:
   ```python
   from agent_core.tools.registry import register_tool

   @register_tool("check_inventory_balance")
   def check_inventory_balance(item_sku: str) -> dict:
       """Kiểm tra số lượng tồn kho theo mã SKU từ hệ thống ERP."""
       # Gọi REST API hoặc DB nội bộ của khách hàng
       return {"sku": item_sku, "available_quantity": 42}
   ```
3. Khai báo tên công cụ `"check_inventory_balance"` vào mục `tools` của Agent tương ứng trong `agents.yaml`.

---

### Bước 6: Kiểm Thử Định Tuyến (Eval Harness)
Chạy bộ test định tuyến chuyên biệt với bộ dữ liệu của khách hàng:

```bash
# Chạy bộ test định tuyến dựa trên eval_set.jsonl của pack
pytest tests/unit/test_agent_builder.py -v
```

---

### Bước 7: Triển Khai Hạ Tầng GCP (Terraform Deployment)
1. Đăng nhập vào GCP Project của khách hàng:
   ```bash
   gcloud config set project "customer-gcp-project-id"
   gcloud auth application-default login
   ```
2. Cấu hình biến môi trường tại `deployment/terraform/terraform.tfvars`:
   ```hcl
   project_id             = "customer-gcp-project-id"
   region                 = "asia-southeast1"
   domain_pack_id         = "customer_domain_name"
   allowed_sso_domains    = "customer.com,subsidiary.customer.com"
   knowledge_backend      = "bigquery" # hoặc "vertex_ai_search"
   redis_enabled          = true
   redis_memory_size_gb   = 1
   min_instance_count     = 1
   max_instance_count     = 10
   ```
3. Khởi tạo và áp dụng cấu hình Terraform:
   ```bash
   cd deployment/terraform
   terraform init
   terraform apply -auto-approve
   ```

---

## 3. Bảng Kiểm Tra Sẵn Sàng Vận Hành (Go-Live Checklist)

| Tiêu chuẩn | Mô tả kiểm tra | Đạt chuẩn |
| :--- | :--- | :--- |
| **Domain Pack Metadata** | `pack.yaml` có `min_core_version: "2.0.0"`, `entry_agent` trỏ đúng agent gốc | ✅ Bắt buộc |
| **Tool Registry Integrity** | 100% tool trong `agents.yaml` đã được đăng ký bằng `@register_tool` | ✅ Bắt buộc |
| **Prompt Injection Guard** | `INDIRECT_PROMPT_INJECTION_DEFENSE_INSTRUCTION` được tự động tiêm | ✅ Bắt buộc |
| **SSO Domain Shielding** | `ALLOWED_SSO_DOMAINS` đã được điền chính xác domain email công ty khách hàng | ✅ Bắt buộc |
| **Knowledge Store Backend** | Bảng BigQuery hoặc Vertex AI Search Datastore đã nạp dữ liệu và cấu hình đúng | ✅ Bắt buộc |
| **Facts & Obligations** | Các bảng `enterprise_facts` và `contract_obligations` đã sẵn sàng | ✅ Bắt buộc |
| **Telemetry Privacy** | `TELEMETRY_ANONYMIZE_USERS=true`, `TELEMETRY_INCLUDE_QUERY=false` | ✅ Bắt buộc |
| **Production Healthz** | `GET /healthz` trả về `core_version="2.0.0"` và đúng `pack_id` | ✅ Bắt buộc |

---

## 4. Xử Lý Sự Cố Thường Gặp Khi Triển Khai (Troubleshooting)

### Sự cố 1: Khởi động thất bại với lỗi `CoreIncompatibleError`
- **Nguyên nhân:** File `pack.yaml` khai báo `min_core_version` cao hơn phiên bản `CORE_VERSION` hiện hành.
- **Xử lý:** Kiểm tra `agent_core.__init__.py` và cập nhật `min_core_version: "2.0.0"` trong `pack.yaml`.

### Sự cố 2: Lỗi `ToolNotFoundError: Tool xyz not found in registry`
- **Nguyên nhân:** Tên tool trong `agents.yaml` viết sai chính tả hoặc chưa được trang bị decorator `@register_tool("xyz")`.
- **Xử lý:** Kiểm tra lại danh sách tool đã đăng ký trong `agent_core/tools/registry.py` hoặc import plugin chứa tool vào runtime.

### Sự cố 3: Người dùng không thể xem dữ liệu dù đã đăng nhập SSO
- **Nguyên nhân:** Email người dùng chưa được cấp quyền trong `user_role_mappings` của `systems.yaml` và không có claim `roles` trong token.
- **Xử lý:** Bổ sung email người dùng vào `user_role_mappings` hoặc thêm tài khoản vào danh mục phân quyền tương ứng.
