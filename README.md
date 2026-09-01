# Enterprise Autonomous Agent Platform (`agent_core`)

Nền tảng **AI Agent Đa Tầng Doanh Nghiệp (Multi-Tier Enterprise Autonomous Agent Platform)** xây dựng trên hệ sinh thái **Google Cloud Vertex AI, Gemini 2.5 / 3, BigQuery Serverless Vector Search, Google ADK và Model Context Protocol (MCP)**.

Hệ thống được thiết kế theo mô hình **Độc Lập Hạ Tầng (Infrastructure-Isolated Deployment)**: mỗi khách hàng sở hữu một GCP Project riêng biệt, đảm bảo an toàn dữ liệu tuyệt đối và khả năng tùy biến linh hoạt thông qua kiến trúc **Domain Pack**.

---

## 🌟 Kiến Trúc Cốt Lõi (Platform Architecture)

```
                                  ┌─────────────────────────────┐
                                  │   Người Dùng / Doanh Nghiệp │
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
                                  │    Semantic Cache Layer     │ ──[ HIT (Sim >= 0.92) ]──► [ Trả lời tức thì ]
                                  │    (Redis / In-Memory)      │                             (Tiết kiệm 100% Token)
                                  └──────────────┬──────────────┘
                                                 │ [ MISS ]
                                                 ▼
                                  ┌─────────────────────────────┐
                                  │      Agent Builder          │ ◄── [ Domain Pack: pack.yaml, agents.yaml ]
                                  │   (agent_core.builder)      │
                                  └──────────────┬──────────────┘
                                                 │ (Dynamic ADK Agents)
                                                 ▼
                                  ┌─────────────────────────────┐
                                  │    Root Entry Orchestrator  │ ◄──► [ Vertex AI Memory Bank ]
                                  │   (Fast Routing Model)      │
                                  └──────────────┬──────────────┘
                 ┌───────────────────────────────┼──────────────────────────────┐
                 ▼                               ▼                              ▼
  ┌─────────────────────────────┐ ┌─────────────────────────────┐ ┌─────────────────────────────┐
  │   Specialist Sub-Agent 1    │ │   Specialist Sub-Agent 2    │ │   Specialist Sub-Agent 3    │
  │     (Fast / Self-Service)   │ │    (Enterprise RAG / MCP)   │ │  (Reasoning / Diagnostics)  │
  └──────────────┬──────────────┘ └──────────────┬──────────────┘ └──────────────┬──────────────┘
          ┌──────┴──────┐                 ┌──────┴──────┐                 ┌──────┴──────┐
          ▼             ▼                 ▼             ▼                 ▼             ▼
    [Case Tool]    [L1 Facts]       [Enterprise    [Email Draft]     [Plugin Tool]   [L3 Obligations]
    (Firestore)    (BigQuery)        RAG MCP]                         (Log/Plugin)      (Registry)
```

---

## 📦 1. Kiến Trúc Domain Pack (Domain Pack System)

`agent_core` tách rời hoàn toàn mã nguồn xử lý logic cốt lõi khỏi cấu hình nghiệp vụ. Để phục vụ nhiều lĩnh vực khách hàng khác nhau:

- **Mã nguồn Core (`agent_core/`)**: Chịu trách nhiệm về bảo mật SSO, Semantic Caching, Tool Registry, Quản lý phiên làm việc ADK, Telemetry, và Dynamic Agent Builder.
- **Gói nghiệp vụ (`domain_packs/`)**: Khai báo danh sách Agent, mô hình AI, chỉ dẫn nghiệp vụ (Instructions), danh mục sự cố (`case_schema.yaml`), và các hệ thống dữ liệu (`systems.yaml`).

### Các Gói Nghiệp Vụ Có Sẵn:
- **`domain_packs/it-helpdesk/`**: Gói giải pháp hỗ trợ kỹ thuật IT, tra cứu quy trình ERP/HRM/CRM, phân tích nguyên nhân sự cố (RCA) và rà soát hợp đồng SLA. Xem [Tài liệu IT Helpdesk](domain_packs/it-helpdesk/README.md).
- **`domain_packs/_template/`**: Gói mẫu khung chuẩn dùng để khởi tạo nhanh bất kỳ domain mới nào (Customer Support, Pháp chế, Tài chính). Xem [Hướng dẫn tạo Domain Pack](domain_packs/README.md).

---

## 🛡️ 2. An Toàn Thông Tin & Phòng Thủ Injection

| Lớp bảo vệ | Chi tiết kỹ thuật |
| :--- | :--- |
| **Fail-Closed Prompt Injection Defense** | Mọi instruction của tất cả các Agent đều được tự động tiêm chỉ dẫn phòng vệ gián tiếp (`INDIRECT_PROMPT_INJECTION_DEFENSE_INSTRUCTION`), coi mọi dữ liệu từ RAG, log, hợp đồng là untrusted data. |
| **Xác thực Google OIDC & RBAC** | Kiểm tra chữ ký số qua Google JWKS Certs, chặn tài khoản cá nhân `@gmail.com` bằng `ALLOWED_DOMAINS`, hỗ trợ phân quyền Role-Based Access Control chặt chẽ. |
| **Zero-Trust Case Isolation (IDOR)** | Người dùng chỉ có quyền tra cứu và cập nhật ticket/case của chính mình. Chỉ tài khoản đặc quyền (`it_admin`, `support_agent`) mới được xem toàn bộ. |
| **Bảo mật Production Endpoint** | Tự động vô hiệu hóa toàn bộ Swagger UI (`/docs`), ReDoc (`/redoc`) và OpenAPI schema trên môi trường Production (`ENVIRONMENT=production` hoặc `K_SERVICE`). |

---

## ⚡ 3. Tối Ưu Hiệu Năng & Chi Phí Hạ Tầng

- **Redis Multi-Tenant Vector Semantic Cache**: Cắt giảm $\approx 100\%$ chi phí token và độ trễ xuống $\sim 20\text{ms}$ cho các câu hỏi phổ biến với cơ chế candidate-set cosine matching.
- **Dual-Engine Enterprise Knowledge Store**: Chuyển đổi linh hoạt qua biến môi trường `KNOWLEDGE_BACKEND`:
  - `bigquery` (Mặc định Data Warehouse): Serverless Vector Search với IVF Index, SQL pre-filtering, 100% Zero-Data Egress, chi phí duy trì **0 USD/tháng** khi rảnh rỗi.
  - `vertex_ai_search` (Managed Enterprise Grounding): Tích hợp trực tiếp Google Cloud Vertex AI Search Discovery Engine, hỗ trợ OCR tài liệu đa định dạng, extractive segments và trích dẫn chuẩn xác.
  - `in_memory`: Dành cho môi trường phát triển local và CI/CD siêu tốc.
- **L1 Facts Registry & L3 Obligations**: Tra cứu thông số kỹ thuật và cam kết pháp lý với tốc độ và độ chính xác xác định tuyệt đối (Deterministic Point-Lookup), loại bỏ hoàn toàn hiện tượng ảo giác (hallucination).

---

## 🚀 4. Hướng Dẫn Vận Hành & Khởi Chạy

### Cài Đặt Môi Trường
```bash
# Cài đặt công cụ quản lý uv và các thư viện phụ thuộc
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync
```

### Chạy Kiểm Thử Toàn Diện (269 Test Cases)
```bash
uv run pytest tests/ -v
```

### Khởi Chạy API Server
```bash
# Chọn domain pack và backend tri thức
export DOMAIN_PACK="it-helpdesk"
export KNOWLEDGE_BACKEND="bigquery" # hoặc "vertex_ai_search"
export ENVIRONMENT="development"
uv run python -m uvicorn agent_core.fast_api_app:app --host 0.0.0.0 --port 8000
```

### Kiểm Tra Trạng Thái Hệ Thống (`/healthz`)
```bash
curl http://localhost:8000/healthz
# Response:
# {"status":"healthy","service":"it-helpdesk-agent","core_version":"2.0.0","pack_id":"it-helpdesk","pack_version":"1.0.0","timestamp":...}
```

---

## 🚢 5. Triển Khai Cho Khách Hàng Mới (Multi-Customer Deployment)

Hệ thống tuân thủ nguyên tắc **Cách ly bằng biên giới hạ tầng (Infrastructure Isolation)**:
1. Tạo một Google Cloud Project riêng biệt cho khách hàng mới.
2. Thiết lập cấu hình biến môi trường (`PROJECT_ID`, `AI_ASSETS_BUCKET`, `DOMAIN_PACK`, `KNOWLEDGE_BACKEND`).
3. Triển khai Terraform stack trong thư mục `deployment/terraform/` lên Cloud Run.
4. Tham khảo tài liệu chi tiết tại [Runbook Onboarding Khách Hàng](Runbook_Onboarding_Khach_Hang.md).

