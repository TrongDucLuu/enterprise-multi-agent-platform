# IT Helpdesk Domain Pack

Gói nghiệp vụ **IT Helpdesk** xây dựng trên nền tảng `agent_core`, cung cấp giải pháp hỗ trợ kỹ thuật và vận hành hệ thống thông minh, phân cấp 3 mức độ (3-Tier Support Architecture).

---

## 🏛️ Cấu Trúc Phân Cấp (3-Tier Support Architecture)

```
                     ┌─────────────────────────────┐
                     │  root_triage_orchestrator   │
                     │      (Gemini 2.5 Flash)     │
                     └──────────────┬──────────────┘
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
┌───────────────────────┐ ┌───────────────────┐ ┌───────────────────────┐
│ l1_selfservice_agent  │ │l2_enterprise_rag  │ │l3_deep_diagnostics    │
│  - FAQ / Wi-Fi / VPN  │ │  - ERP / HRM SOP  │ │  - RCA Log Analyzer   │
│  - Password Reset     │ │  - RAG MCP Search │ │  - SLA Review & DPA   │
│  - L1 Facts Registry  │ │  - Email Draft    │ │  - L3 Obligations     │
└───────────────────────┘ └───────────────────┘ └───────────────────────┘
```

### 1. `l1_selfservice_agent` (Mức 1 — Tự phục vụ & FAQ)
- **FAQ & Chính sách IT:** Mật khẩu, VPN, Wi-Fi doanh nghiệp, máy in văn phòng.
- **Tra cứu Fact Cứng (L1 Facts Registry):** Tra cứu trực tiếp ngưỡng số học, SLA, port/IP máy chủ qua công cụ `lookup_fact`.
- **Tự phục vụ:** Hướng dẫn từng bước reset mật khẩu Active Directory, Google Workspace, Okta.
- **Quản lý Sự cố:** Tiếp nhận lỗi, tạo ticket (`create_helpdesk_ticket`), tra cứu (`list_user_tickets`, `get_ticket_details`), cập nhật (`update_ticket_status`).

### 2. `l2_enterprise_rag_agent` (Mức 2 — Tri thức Nghiệp vụ Enterprise)
- **Enterprise RAG MCP:** Tra cứu quy trình và SOP các hệ thống ERP (SAP/Oracle), HRM (Workday), CRM (Salesforce).
- **Trích dẫn & Toàn vẹn:** Tự động gọi `get_system_manual` khi tài liệu bị cắt ngắn (`is_truncated=True`) và trích dẫn chuẩn `[Nguồn: ... | Mã: ...]`.
- **Soạn thảo Email:** Tạo bản thảo email hướng dẫn chuẩn mực qua `draft_email_response`.

### 3. `l3_deep_diagnostics_agent` (Mức 3 — Chẩn đoán Chuyên sâu & Pháp lý IT)
- **Root Cause Analysis (RCA):** Phân tích log và stack trace hệ thống (`analyze_system_logs_for_rca`), phát hiện lỗi OOM, DB Connection Leak, Deadlock.
- **Pháp lý IT & SLA (L3 Obligations Registry):** Tra cứu cam kết pháp lý (`get_obligation`, `list_contract_obligations`) và rà soát hợp đồng (`review_it_contract_sla`).

---

## 📁 Cấu Trúc Tệp Gói Nghiệp Vụ

```
domain_packs/it-helpdesk/
├── pack.yaml          # Khai báo định danh, phiên bản, min_core_version: "2.0.0"
├── agents.yaml        # Cấu hình 4 agents, instructions, models và công cụ
├── case_schema.yaml   # Phân loại sự cố (categories), mức độ ưu tiên, trạng thái
├── systems.yaml       # Danh mục các hệ thống ERP, HRM, CRM và quyền truy cập
├── eval_set.jsonl     # Bộ dữ liệu 7+ kịch bản kiểm thử định tuyến
└── README.md          # Tài liệu hướng dẫn chuyên biệt cho IT Helpdesk
```

---

## 🚀 Kích Hoạt & Kiểm Thử

Để chạy hệ thống với Domain Pack này:
```bash
export DOMAIN_PACK="it-helpdesk"
uv run pytest tests/unit/test_agent_builder.py -v
```
