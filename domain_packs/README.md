# Hướng Dẫn Phát Triển Domain Pack (Domain Pack Developer Guide)

Kiến trúc **Domain Pack** cho phép mở rộng nền tảng `agent_core` sang bất kỳ lĩnh vực nghiệp vụ nào (Customer Support, Tài chính, Pháp chế, Vận hành) mà **hoàn toàn không cần sửa đổi mã nguồn cốt lõi**.

---

## 4 Bước Tạo Mới Domain Pack

### Bước 1: Khởi tạo thư mục từ Template
Sao chép thư mục `domain_packs/_template/` thành thư mục domain mới của bạn:
```bash
cp -r domain_packs/_template domain_packs/<your-domain-name>
```
*Ví dụ:* `domain_packs/hr-service/` hoặc `domain_packs/customer-support/`

### Bước 2: Khai báo định danh (`pack.yaml`)
Cấu hình metadata của gói:
```yaml
id: "customer-support"
name: "Customer Support Automation Pack"
version: "1.0.0"
min_core_version: "2.0.0"
description: "AI Agent tự động hóa chăm sóc khách hàng đa kênh"
entry_agent: "root_orchestrator"
```

### Bước 3: Định nghĩa cấu trúc Agent & Phân quyền (`agents.yaml`)
Thiết lập danh sách Agent, mô hình AI (`fast` hoặc `reasoning`), lời nhắc định hướng nghiệp vụ (instruction) và các công cụ được phép sử dụng:
```yaml
agents:
  root_orchestrator:
    name: "root_orchestrator"
    description: "Điều phối và phân luồng yêu cầu khách hàng"
    model_type: "fast"
    instruction: |
      Bạn là Trưởng nhóm Chăm sóc Khách hàng. Phân tích yêu cầu và định tuyến cho chuyên viên.
    tools: []
    sub_agents:
      - support_specialist
```
> **Lưu ý bảo mật:** `agent_core` sẽ tự động tiêm chỉ dẫn phòng thủ tấn công **Indirect Prompt Injection Defense** vào tất cả các Agent khi khởi tạo.

### Bước 4: Cấu hình Schema & Dữ liệu (`case_schema.yaml`, `systems.yaml`, `eval_set.jsonl`)
- `case_schema.yaml`: Khai báo các phân loại (`categories`), mức độ ưu tiên (`priorities`), trạng thái (`statuses`) và các cấp xử lý (`tiers`).
- `systems.yaml`: Khai báo danh mục các hệ thống nội bộ cần tra cứu và phân quyền vai trò (`admin_roles`).
- `eval_set.jsonl`: Danh sách câu hỏi kiểm thử định tuyến để đo lường độ chính xác.

---

## Kích Hoạt Domain Pack

Chỉ định Domain Pack cần nạp qua biến môi trường:
```bash
export DOMAIN_PACK="<your-domain-name>"
```
Ví dụ:
```bash
export DOMAIN_PACK="it-helpdesk"
uv run python -m uvicorn agent_core.fast_api_app:app --port 8000
```
