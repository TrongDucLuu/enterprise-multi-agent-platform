# IT Helpdesk Multi-Agent AI System (Production-Ready)

Hệ thống **IT Helpdesk Multi-Agent AI** thông minh, phân cấp 3 mức độ (3-Tier Support), ứng dụng các công nghệ tiên tiến nhất của hệ sinh thái **Google AI (Google ADK, Gemini 3, Model Context Protocol - MCP, Vertex AI Memory Bank)** và sẵn sàng triển khai trên **Google Cloud Run**.

---

## 🌟 3 Mức Độ Hỗ Trợ Kỹ Thuật (3-Tier Architecture)

### 🟢 Mức 1 — Giao Tiếp & Hỗ Trợ Cơ Bản (`l1_selfservice_agent`)
- **FAQ & Chính sách IT:** Giải đáp chính sách bảo mật, chuẩn mật khẩu, hướng dẫn kết nối VPN, Wi-Fi, máy in.
- **Tự phục vụ (Self-Service):** Hướng dẫn từng bước reset mật khẩu Active Directory/Google Workspace/Okta, mở khóa tài khoản.
- **Tiếp nhận & Phân loại:** Lắng nghe mô tả lỗi, tự động tạo ticket và phân loại mức độ ưu tiên (Low, Medium, High, Critical).

### 🔵 Mức 2 — Tra cứu Tài liệu (RAG) & Hệ thống Doanh nghiệp (`l2_enterprise_rag_agent`)
- **Enterprise RAG MCP:** Tích hợp tra cứu sâu vào cơ sở tri thức hệ thống **ERP** (SAP/Oracle PO & kế toán), **HRM** (Workday chấm công & onboarding), **CRM** (Salesforce lead sync & territory transfer).
- **Đọc hiểu & Tóm tắt tài liệu dài:** Rút trích điểm cốt lõi và các bước hành động từ các file tài liệu hướng dẫn kỹ thuật dài.
- **Soạn thảo Email & Cập nhật Ticket:** Soạn email phản hồi chuẩn hóa, lịch sự và cập nhật trạng thái ticket tự động.

### 🟣 Mức 3 — Phân tích & Suy luận Chuyên sâu (`l3_deep_diagnostics_agent`)
- **Mô hình năng lực cao:** Sử dụng `gemini-3-pro-preview` chuyên trách suy luận logic phức tạp.
- **Root Cause Analysis (RCA):** Phân tích log file, stack traces, phát hiện mã lỗi (OOM, Deadlock, Connection Timeout, Auth Failure) và lập báo cáo RCA chuyên sâu.
- **Pháp lý IT & Rà soát SLA Hợp đồng:** Đánh giá cam kết Uptime, thời gian phản hồi MTTR, điều khoản bồi thường (Service Credits), và tuân thủ bảo vệ dữ liệu (DPA, NDA, GDPR).

---

## 🏗️ Sơ Đồ Kiến Trúc Hệ Thống

```
                                  [ Người dùng / Nhân viên ]
                                              │
                                              ▼
                             [ root_triage_orchestrator ] ◄──► [ Vertex AI Memory Bank ]
                                              │
                ┌─────────────────────────────┼────────────────────────────┐
                ▼                             ▼                            ▼
      [ l1_selfservice_agent ]     [ l2_enterprise_rag_agent ]   [ l3_deep_diagnostics_agent ]
        (Gemini 3 Flash)             (Gemini 3 Flash)              (Gemini 3 Pro Preview)
                │                             │                            │
        ┌───────┴───────┐             ┌───────┴────────┐           ┌───────┴────────┐
        ▼               ▼             ▼                ▼           ▼                ▼
  Ticketing Tool   Memory Tool     RAG MCP Server   Email Tool  Log RCA Tool    SLA & Legal Tool
```

---

## 🚀 Hướng Dẫn Cài Đặt & Chạy Cục Bộ

### 1. Cài đặt Dependencies
```bash
uv sync
```

### 2. Chạy Kiểm Thử Tự Động (Unit Tests)
```bash
uv run pytest tests/ -v
```

### 3. Chạy Thử Nghiệm Tương Tác CLI (Local Interactive Mode)
```bash
uv run python main.py --mode cli
```

### 4. Khởi Chạy Web Server (FastAPI + ADK Web UI)
```bash
uv run python main.py --mode serve --port 8080
```
Truy cập giao diện Web tại: `http://localhost:8080`

---

## ☁️ Triển Khai Lên Google Cloud (Production)

### 1. Khởi tạo Hạ tầng bằng Terraform
```bash
cd deployment/terraform
terraform init
terraform plan -var="project_id=YOUR_PROJECT_ID"
terraform apply -auto-approve -var="project_id=YOUR_PROJECT_ID"
```

### 2. Build Container & Deploy Cloud Run
```bash
make docker-deploy PROJECT_ID=YOUR_PROJECT_ID
```
