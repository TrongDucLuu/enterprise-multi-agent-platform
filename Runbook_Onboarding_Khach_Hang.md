# Runbook: Onboarding Khách Hàng & Vận Hành Ingestion Pipeline

> **Dành cho:** Solutions Architects, DevOps Engineers, và System Administrators  
> **Hệ thống:** IT Helpdesk Multi-Agent AI System (`it-helpdesk-agent`)  
> **Phiên bản tài liệu:** 2.0 (Config-Driven & Tiered Ingestion Architecture)

---

## 1. Tổng Quan Kiến Trúc Onboarding

Hệ thống `it-helpdesk-agent` được thiết kế theo kiến trúc **Config-Driven**. Khi triển khai cho một khách hàng doanh nghiệp mới (hoặc mở rộng thêm hệ thống nghiệp vụ như MES, HIS, Core Banking, WMS...), toàn bộ cấu hình hệ thống, vai trò RBAC, chiến lược chunking và xử lý tài liệu được khai báo tập trung tại [`config/systems.yaml`](file:///Users/luuduc/.gemini/antigravity/scratch/it-helpdesk-agent/config/systems.yaml) mà **không cần sửa hay biên dịch lại bất kỳ dòng mã nguồn nào**.

```
                           +------------------------------------+
                           |        config/systems.yaml         |
                           +------------------------------------+
                                      |              |
                +---------------------+              +---------------------+
                |                                                          |
                v                                                          v
   [Đường Đọc: Runtime Agent]                               [Đường Ghi: Ingestion Pipeline]
   - Security-Trimming (IDOR/RBAC)                          - Structured Document Parser
   - Dynamic System Filter                                  - Tiered Chunking Strategy
   - Prompt Context Injection                               - CDC + BigQuery MERGE Upsert
```

---

## 2. Quy Trình Onboarding Khách Hàng Mới (Step-by-Step)

### Bước 1: Khai báo Danh mục Hệ thống & Phân quyền RBAC

Chỉnh sửa tệp `config/systems.yaml` để định nghĩa các hệ thống mục tiêu của khách hàng:

```yaml
# Các vai trò quản trị viên có quyền truy cập toàn bộ các hệ thống
shared_admin_roles:
  - "it_admin"
  - "support_agent"
  - "sysadmin"

# Danh mục hệ thống nghiệp vụ của khách hàng
systems:
  ERP:
    display_name: "Enterprise Resource Planning (SAP S/4HANA)"
    vendor_examples: "SAP / Oracle NetSuite"
    description: "Hệ thống quản lý tài chính, kế toán, chuỗi cung ứng và mua sắm vật tư"
    common_issues:
      - "Lỗi khóa tài khoản người dùng do nhập sai mật khẩu 3 lần"
      - "Lỗi hạch toán hóa đơn và kỳ kế toán bị đóng (FI-GL)"
      - "Lỗi duyệt đơn mua hàng PO qua giao dịch ME29N"
    roles:
      - "erp_user"
      - "accountant"
      - "procurement_lead"

  HRM:
    display_name: "Human Resource Management (Workday)"
    vendor_examples: "Workday / BambooHR"
    description: "Hệ thống quản trị nhân sự, chấm công, nghỉ phép và bảo hiểm y tế"
    common_issues:
      - "Lỗi đồng bộ dữ liệu chấm công vân tay vào kỳ lương"
      - "Quy trình phê duyệt nghỉ thai sản / nghỉ phép năm"
    roles:
      - "employee"
      - "hr_specialist"
      - "hr_manager"

  MES:
    display_name: "Manufacturing Execution System"
    vendor_examples: "Siemens Opcenter / Rockwell FactoryTalk"
    description: "Hệ thống điều hành và giám sát dây chuyền sản xuất nhà máy"
    common_issues:
      - "Lỗi mất kết nối OPC-UA tới thiết bị PLC trạm dập"
      - "Lỗi đồng bộ lệnh sản xuất (Work Order) từ ERP xuống chuyền"
    roles:
      - "factory_operator"
      - "plant_engineer"
      - "production_supervisor"
```

> [!IMPORTANT]
> **Quy tắc đặt tên:** Tên hệ thống (key) chỉ bao gồm chữ cái in hoa và số (`[A-Z0-9]+`), độ dài tối đa 20 ký tự. Từ khóa `ALL` là từ khóa dành riêng cho Security Trimming, nghiêm cấm đặt tên hệ thống là `ALL`.

---

### Bước 2: Cấu hình Chiến Lược Chunking & Document Processing

Tại cùng tệp `config/systems.yaml`, cấu hình pipeline chunking phù hợp với đặc thù dữ liệu tài liệu của khách hàng:

```yaml
# Cấu hình chiến lược phân mảnh tri thức (Chunking Pipeline)
chunking:
  default_strategy: "auto"        # "auto" | "fixed" | "semantic"
  max_chunk_size: 1200            # Độ dài chunk tối đa (ký tự)
  overlap: 150                    # Độ dài gối đầu giữa các chunk
  well_structured_max_section_ratio: 0.65  # Ngưỡng tỷ lệ tối đa của 1 section (65%)
  well_structured_min_avg_section_length: 100 # Độ dài trung bình tối thiểu của section

  # Cho phép ghi đè cấu hình theo từng hệ thống nghiệp vụ đặc thù
  systems:
    HRM:
      strategy: "semantic"        # HRM sử dụng cờ semantic chunking
    MES:
      max_chunk_size: 800         # Sổ tay bảo trì MES chia nhỏ 800 ký tự
      overlap: 100

# Cấu hình bộ trích xuất định dạng tài liệu (Document Processing)
document_processing:
  pdf_parser: "pypdf_flat"        # "pypdf_flat" | "document_ai"
  document_ai_processor_id: ""    # Bắt buộc nếu dùng document_ai (projects/.../processors/...)
  document_ai_timeout_seconds: 60
  document_ai_max_retries: 2
```

#### Hướng dẫn chọn PDF Parser:
1. **`pypdf_flat` (Mặc định - Chi phí $0):**
   - Phù hợp với tài liệu PDF dạng văn bản một cột phẳng, tài liệu nội bộ xuất từ Word.
2. **`document_ai` (Google Cloud Document AI Layout Parser - $10 / 1.000 trang):**
   - Khuyên dùng cho khách hàng có tài liệu kỹ thuật phức tạp: định dạng nhiều cột (multi-column), bảng biểu phức tạp, heading phân cấp sâu.
   - Bắt buộc khai báo `document_ai_processor_id`. Hệ thống sẽ **Fail-Closed** nếu cấu hình thiếu ID hoặc API lỗi vượt quá số lần retry.

---

### Bước 3: Chuẩn Bị & Nạp Dữ Liệu (Ingestion)

Tập hợp tài liệu tri thức của khách hàng theo định dạng hỗ trợ:
- `.md` / `.txt` (Hỗ trợ cấu trúc heading `#`, `##`, `###`)
- `.docx` (Hỗ trợ cấu trúc Style Heading 1, 2, 3)
- `.pdf` (Trích xuất văn bản phẳng hoặc Document AI Layout)
- `.jsonl` (Dữ liệu bài viết có cấu trúc sẵn)

Chạy lệnh nạp tài liệu vào BigQuery Vector Search:

```bash
# Nạp từ thư mục tài liệu với hệ thống mặc định là ERP
python scripts/ingest_knowledge_base.py \
    --project-id="your-gcp-project-id" \
    --dataset-id="it_helpdesk_kb" \
    --table-name="knowledge_articles" \
    --data-dir="/path/to/customer/docs" \
    --default-system="ERP"
```

---

## 3. Cập Nhật Chiến Lược Chunking Cho Khách Hàng Đang Chạy

Khi một khách hàng đang hoạt động yêu cầu đổi chiến lược chunking (ví dụ: từ `fixed` sang `auto` hoặc điều chỉnh `max_chunk_size` từ 1200 xuống 800):

```
                                  ĐỔI CẤU HÌNH CHUNKING
                                            │
                                            ▼
                           ┌─────────────────────────────────┐
                           │   Chạy lại Ingestion Pipeline   │
                           │   (scripts/ingest_knowledge_base)│
                           └────────────────┬────────────────┘
                                            │
                     ┌──────────────────────┴──────────────────────┐
                     │                                             │
                     ▼                                             ▼
       ┌───────────────────────────┐                 ┌───────────────────────────┐
       │   Các Chunk có ID mới    │                 │   Các Chunk mồ côi (cũ)   │
       │  MERGE Upsert vào BigQuery│                 │  CLEANUP ORPHANED CHUNKS  │
       └───────────────────────────┘                 └───────────────────────────┘
```

### Hiện tượng Chunk mồ côi (Orphaned Chunks):
- Khi tài liệu được cắt nhỏ hơn, số lượng chunk sinh ra sẽ tăng lên (ví dụ: tài liệu `sap_guide.md` trước đây sinh 2 chunk `ERP-KB-AAA`, `ERP-KB-BBB`; sau khi đổi kích thước sinh ra 4 chunk `ERP-KB-C1`, `ERP-KB-C2`, `ERP-KB-C3`, `ERP-KB-C4`).
- Nếu chỉ dùng `MERGE` đơn thuần, 2 chunk cũ (`AAA`, `BBB`) vẫn tồn tại trong BigQuery, gây ô nhiễm kết quả tìm kiếm ngữ nghĩa RAG (Vector Search trả về cả phiên bản chunk cũ và mới).

### Cơ chế dọn dẹp tự động của Hệ thống:
1. `ingest_knowledge_base.py` tự động kích hoạt hàm `cleanup_orphaned_chunks(source_uris, active_article_ids)`.
2. Hàm sẽ thực hiện truy vấn DELETE đối với tất cả các bản ghi có `source_uri` nằm trong danh sách tài liệu vừa nạp nhưng `id` không nằm trong tập `active_article_ids` mới.
3. **Thứ tự thực thi:** MERGE hoàn tất $\rightarrow$ Dọn dẹp Chunk mồ côi $\rightarrow$ Đảm bảo tri thức luôn nhất quán 100%.

```bash
# Lệnh chạy cập nhật lại tri thức cho khách hàng:
python scripts/ingest_knowledge_base.py \
    --project-id="$PROJECT_ID" \
    --dataset-id="$DATASET_ID" \
    --table-name="knowledge_articles" \
    --data-dir="./knowledge_base_files"
```

---

## 4. Bảng Kiểm Tra An Toàn Vận Hành (Operational Checklist)

| Hạng mục kiểm tra | Tiêu chuẩn đánh giá | Trạng thái |
| :--- | :--- | :--- |
| **Config Schema Validation** | Không chứa ký tự đặc biệt, không dùng key `ALL`. |  Bắt buộc |
| **Fail-Closed Protection** | Cấu hình sai YAML hoặc thiếu Processor ID sẽ dừng nạp ngay lập tức. |  Bắt buộc |
| **SSO & RBAC Alignment** | Tất cả vai trò trong `systems.yaml` phải khớp với claim SSO OIDC của khách hàng. |  Bắt buộc |
| **BigQuery Vector Index** | Bảng BigQuery có vector index `COSINE` kích hoạt trên cột `embedding`. |  Bắt buộc |
| **Deduplication & CDC** | Hash `content_hash` được cập nhật chính xác, không trùng lặp ID trong staging. |  Bắt buộc |
