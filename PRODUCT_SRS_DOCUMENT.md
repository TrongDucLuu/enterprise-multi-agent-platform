# TÀI LIỆU ĐẶC TẢ YÊU CẦU SẢN PHẨM & NGHIỆP VỤ (PRODUCT REQUIREMENTS & SOFTWARE SPECIFICATION - SRS)
## Hệ thống Trợ lý Trí tuệ Nhân tạo Đa Tác tử Doanh nghiệp (Enterprise Multi-Agent Platform)

| Thuộc tính | Giá trị | Thuộc tính | Giá trị |
|---|---|---|---|
| **Tên Sản phẩm** | Enterprise Multi-Agent AI Platform | **Phiên bản SRS** | `2.2.0-Enterprise` |
| **Loại Tài liệu** | Product Spec & Functional SRS | **Chủ quản Sản phẩm** | Lead PM & Senior BA |
| **Đối tượng Áp dụng** | Toàn bộ Doanh nghiệp / Khối IT & Ops | **Kiến trúc Nền tảng** | Google ADK & Gemini 2.5 / 1.5 |
| **Trạng thái Phê duyệt** | Approved for Production | **Ngày ban hành** | 02/09/2026 |

---

## 1. Tổng Quan Sản Phẩm & Tầm Nhìn Chiến Lược (Product Overview)

### 1.1. Bối cảnh & Thách thức Doanh nghiệp (Business Problem Statement)
Tại các doanh nghiệp hiện đại, khối vận hành IT Helpdesk & Internal Support đóng vai trò sống còn để duy trì năng suất lao động cho hàng ngàn nhân viên. Tuy nhiên, các mô hình Helpdesk truyền thống gặp phải các điểm nghẽn nghiêm trọng:
- **60% – 70% yêu cầu gửi đến là các tác vụ lặp lại cơ bản** (FAQ, reset mật khẩu, mở khóa tài khoản, cài đặt Wi-Fi/VPN).
- **Thời gian phản hồi ban đầu (FRT) và thời gian giải quyết sự cố (MTTR) kéo dài** từ 4 đến 24 giờ.
- **Sự cố nghiệp vụ trên các hệ thống ERP, HRM, CRM** đòi hỏi tra cứu tài liệu hướng dẫn kỹ thuật dài và phân tán.
- **Sự cố gián đoạn hệ thống nghiêm trọng (Downtime)** thiếu công cụ tự động phân tích nhanh nguyên nhân gốc rễ (Root Cause Analysis - RCA).
- **Chi phí nhân sự IT Support gia tăng tuyến tính** theo quy mô nhân sự của công ty.

### 1.2. Tầm nhìn Sản phẩm (Product Vision)
Xây dựng một nền tảng **Trợ lý Đa Tác tử AI Thông minh, Tự chủ và An toàn Cấp Doanh nghiệp (Enterprise Multi-Agent Platform)**, đóng vai trò là điểm tiếp nhận duy nhất (**Single Point of Contact - SPOC**), có khả năng:
1. Giải quyết tự động tức thì các sự cố thông thường (L1 Self-Service).
2. Hỗ trợ tra cứu tri thức nghiệp vụ sâu qua Enterprise RAG với Re-ranker thông minh và cơ chế Fallback chống gián đoạn (L2 Enterprise RAG).
3. Hỗ trợ kỹ sư IT cấp cao chẩn đoán lỗi hạ tầng sâu và rà soát tuân thủ cam kết SLA của nhà cung cấp dịch vụ (L3 Deep Diagnostics).
4. Khả năng mở rộng đa miền nghiệp vụ linh hoạt thông qua kiến trúc **Domain Packs** tách rời hoàn toàn với Core Engine.

### 1.3. Mục tiêu Nghiệp vụ & Chỉ số Đánh giá Hiệu quả (Business Goals & OKRs/KPIs)

| Chỉ số Đo lường (KPI) | Trước khi áp dụng | Mục tiêu sau triển khai | Ý nghĩa Nghiệp vụ & Đóng góp |
|---|---|---|---|
| **Tỷ lệ Tự phục vụ Thành công (FCR)** | < 15% | $\ge 65\%$ | Người dùng tự xử lý thành công ngay ở Mức 1 mà không cần can thiệp con người. |
| **Thời gian Giải quyết Sự cố (MTTR)** | 4.5 giờ | $< 15$ phút (Trung bình) | Giảm thiểu thời gian gián đoạn công việc của nhân viên văn phòng. |
| **Tỷ lệ Phản hồi Tức thì (Cache Hit)** | 0% | $\ge 50\%$ câu hỏi lặp | Lớp Semantic Cache phản hồi $< 50	ext{ ms}$, tiết kiệm 100% token Gemini. |
| **Độ chính xác Báo cáo L3 RCA** | Thủ công (1–2 ngày) | Tức thì ($< 30$ giây) | Phân loại chính xác 6 nhóm lỗi cốt lõi (OOM, DB, Disk, Null, Auth, Network). |
| **Chỉ số Hài lòng Người dùng (CSAT)** | 3.2 / 5.0 | $\ge 4.7 / 5.0$ | Trải nghiệm hội thoại tự nhiên, thông minh, phục vụ 24/7/365. |

---

## 2. Chân Dung Người Dùng & Bên Liên Quan (User Personas)

| Chân dung (Persona) | Vai trò Doanh nghiệp | Nhu cầu & Nỗi đau chính | Kỳ vọng đối với Hệ thống AI |
|---|---|---|---|
| **Nhân viên Văn phòng (Employee)** | Người dùng cuối (End-User) | Quên mật khẩu, lỗi Wi-Fi, không vào được tài khoản, cần hỗ trợ gấp. | Nhận hướng dẫn chi tiết từng bước, tự mở khóa tài khoản tức thì, tạo ticket tự động. |
| **Chuyên viên IT Helpdesk (IT Support / L1-L2)** | Hỗ trợ Kỹ thuật (IT Specialist) | Quá tải các ticket lặp lại, mất thời gian tra cứu manual ERP/HRM/CRM. | AI tự động phân loại, tóm tắt tài liệu nghiệp vụ dài và soạn sẵn email phản hồi. |
| **Kỹ sư SRE / DevOps (SysAdmin / Lead Eng)** | Quản trị Hệ thống (Infrastructure Ops) | Hệ thống sập, log lỗi tràn ngập, áp lực tìm nguyên nhân gốc rễ (RCA) nhanh. | Công cụ bóc tách log tự động, khoanh vùng chính xác module lỗi và đề xuất Workaround. |
| **Chuyên viên Pháp chế (Legal / Compliance)** | Giám sát Tuân thủ (Legal Counsel) | Rà soát thủ công các điều khoản SLA, Uptime, DPA, GDPR trong hợp đồng nhà cung cấp. | Trích xuất tự động Uptime %, MTTR, phát hiện các rủi ro thiếu bồi thường và vi phạm bảo mật. |
| **Giám đốc Công nghệ (CIO / IT Director)** | Lãnh đạo Chiến lược (Executive Sponsor) | Chi phí IT cao, rủi ro lộ lọt dữ liệu, thiếu báo cáo hiệu quả vận hành. | Hệ thống bảo mật Zero-Trust, tối ưu chi phí Serverless và báo cáo minh bạch. |

---

## 3. Phạm Vi Sản Phẩm & Ràng Buộc Nghiệp Vụ (Scope & Constraints)

- **Phạm vi Trong Sản phẩm (In-Scope)**:
  - Tiếp nhận và phân luồng tự động yêu cầu IT Helpdesk qua hội thoại tự nhiên.
  - Hỗ trợ tự phục vụ: hướng dẫn reset mật khẩu, mở khóa tài khoản, thiết lập 2FA/MFA, VPN, Wi-Fi và máy in.
  - Tra cứu tài liệu nghiệp vụ doanh nghiệp (ERP, HRM, CRM) qua giao thức Enterprise RAG MCP và BigQuery Vector Search.
  - Phân tích log lỗi hệ thống (Root Cause Analysis) và rà soát SLA hợp đồng IT.
  - Quản lý phân quyền tri thức đa tầng (**Clearance Levels: Public, Internal, Confidential, Restricted**).
  - Xác thực tập trung Google OIDC với cơ chế Fail-Closed Domain Filtering và phân quyền RBAC.
  - Quản lý vòng đời ticket tích hợp Cloud Firestore với Optimistic Concurrency Control (OCC) và Append-Only Audit Trail.
  - Hỗ trợ kiến trúc Domain Packs đa khách hàng linh hoạt (`_template`, `it-helpdesk`, custom packs).

- **Phạm vi Ngoài Sản phẩm (Out-of-Scope)**:
  - Thay thế hoàn toàn quyết định phê duyệt mua sắm phần cứng hoặc chi ngân sách IT.
  - Tự động thực thi lệnh reset tài khoản trên Domain Controller mà không có sự xác nhận của người dùng.

---

## 4. Đặc Tả Yêu Cầu Chức Năng (Functional Requirements - FRs)

### FR-01: Tầng Tiếp Nhận & Điều Phối Đa Tác Tử Động (Dynamic Root Triage Orchestration)
- **Mã chức năng**: `FR-01-TRIAGE`
- **Tác tử thực thi**: `root_triage_orchestrator` (Gemini 2.5 / Flash Preview).
- **Yêu cầu chi tiết**:
  1. Tiếp nhận hội thoại từ người dùng, nạp lịch sử tương tác và ngữ cảnh thiết bị.
  2. Tự động nhận diện ý định (Intent Recognition) và phân loại định tuyến dựa trên cấu hình khai báo từ active **Domain Pack**:
     - Định tuyến đến L1: Câu hỏi FAQ, quy trình tự phục vụ, yêu cầu tạo ticket sơ bộ.
     - Định tuyến đến L2: Sự cố nghiệp vụ hệ thống ERP (SAP/Oracle), HRM (Workday), CRM (Salesforce).
     - Định tuyến đến L3: Yêu cầu phân tích log lỗi hệ thống (RCA) hoặc rà soát SLA/DPA hợp đồng IT.
  3. Tổng hợp kết quả phản hồi từ các Sub-agent và trả về cho người dùng với văn phong chuyên nghiệp.

### FR-02: Mức 1 - Tự Phục Vụ & Quản Lý Ticket (L1 Self-Service Specialist)
- **Mã chức năng**: `FR-02-L1-SUPPORT`
- **Tác tử thực thi**: `l1_selfservice_agent` (Gemini Flash).
- **Yêu cầu chi tiết**:
  1. Hướng dẫn chi tiết từng bước (Step-by-step) các quy trình Self-Service: Reset mật khẩu Active Directory/Google Workspace/Okta, mở khóa tài khoản, kết nối mạng nội bộ.
  2. Quản lý Ticket tự động: Sử dụng công cụ `create_case` / `create_helpdesk_ticket` tạo ticket mới với ID duy nhất (`TICK-XXXXXXXX` hoặc `CASE-XXXXXXXX`), thiết lập độ ưu tiên (`P1`, `P2`, `P3`, `P4`) và ghi nhận vào Firestore với OCC versioning.
  3. Cung cấp chức năng tra cứu lịch sử ticket cá nhân (`list_user_tickets`) và chi tiết ticket (`get_ticket_details` / `get_case`).

### FR-03: Mức 2 - Tra Cứu Tri Thức Nghiệp Vụ & Enterprise RAG (L2 Enterprise RAG)
- **Mã chức năng**: `FR-03-L2-RAG`
- **Tác tử thực thi**: `l2_enterprise_rag_agent` kết hợp Enterprise RAG MCP.
- **Yêu cầu chi tiết**:
  1. Tra cứu sâu cơ sở tri thức nghiệp vụ qua MCP Tools (`search_enterprise_knowledge`, `get_system_manual`):
     - ERP: Lỗi phân quyền Purchase Order (PO), kỳ kế toán bị khóa, đồng bộ tồn kho.
     - HRM: Lỗi đồng bộ máy chấm công vân tay, khóa kỳ tính lương Payroll, quy trình Onboarding.
     - CRM: Lỗi đồng bộ Lead, vượt hạn ngạch API Limits, chuyển giao Account.
  2. Tích hợp Re-ranker Cross-Encoder với cơ chế Graceful Fallback (BM25 / Cosine) khi model weights không khả dụng.
  3. Kiểm soát phân quyền tài liệu theo Clearance Level ($0 \dots 3$).
  4. Tóm tắt tài liệu kỹ thuật dài (`summarize_long_document`) và soạn thảo email mẫu (`draft_email_response`).

### FR-04: Mức 3 - Phân Tích Nguyên Nhân Gốc Rễ & Pháp Lý IT (L3 Deep Diagnostics)
- **Mã chức năng**: `FR-04-L3-DIAGNOSTICS`
- **Tác tử thực thi**: `l3_deep_diagnostics_agent` (Gemini Pro - High Reasoning Model).
- **Yêu cầu chi tiết**:
  1. Root Cause Analysis (`analyze_system_logs_for_rca`): Phân tích log file, stack trace, phát hiện 6 nhóm dị thường cốt lõi (OOM, DB Connection Exhausted, Network Timeout, Auth Security Failure, Data Corruption Null, Disk I/O Failure) và lập báo cáo RCA chuẩn 4 phần.
  2. Rà soát SLA Hợp đồng IT (`review_it_contract_sla`): Bóc tách cam kết Uptime %, thời gian phản hồi MTTR 2 chiều, phát hiện rủi ro thiếu điều khoản bồi thường (Service Credits), quyền kiểm toán và nghĩa vụ thông báo sự cố rò rỉ dữ liệu (24h–72h).
  3. Kiểm soát quyền RBAC: Chỉ cho phép các vai trò đặc quyền (`it_admin`, `sys_admin`, `devops_engineer`, `compliance_officer`) thực thi.

### FR-05: Lớp Bộ Đệm Ngữ Nghĩa Đa Phân Vùng (Clearance-Aware Semantic Cache Engine)
- **Mã chức năng**: `FR-05-SEMANTIC-CACHE`
- **Yêu cầu chi tiết**:
  1. Đánh giá độ tương đồng ngữ nghĩa câu hỏi bằng Vector Cosine Similarity (ngưỡng mặc định $\ge 0.92$).
  2. Phân vùng bộ đệm theo cấp độ bảo mật (`clearance_level: 0, 1, 2, 3`): Chỉ cache công khai câu hỏi FAQ Turn 1 không dùng tool nhạy cảm. Câu hỏi có tri thức bảo mật cao được cô lập theo user/clearance.
  3. Khi Cache Hit: Trả lời ngay tức thì ($< 50	ext{ ms}$), không tiêu tốn token gọi Gemini.
  4. Quản lý vòng đời bộ đệm: Tự động hết hạn sau 24h (TTL) và thu hồi theo chính sách LRU.

### FR-06: Bảo Mật Định Danh & Phân Quyền Doanh Nghiệp (Enterprise SSO & Zero-Trust RBAC)
- **Mã chức năng**: `FR-06-SSO-RBAC`
- **Yêu cầu chi tiết**:
  1. Xác thực Google Workspace OIDC ID Token chuẩn mực qua JWKS Public Keys của `accounts.google.com`.
  2. Kiểm soát domain Fail-Closed (`ALLOWED_DOMAINS`): Chặn triệt để email ngoài tổ chức và Gmail cá nhân.
  3. Chống tấn công Algorithm Confusion: Cô lập hoàn toàn giữa RS256 (OIDC Prod) và HS256 (Dev Mock Token).
  4. Truyền tải ngữ cảnh người dùng qua `ContextVar` để kiểm tra phân quyền RBAC tại từng tool nhạy cảm.

---

## 5. Đặc Tả Yêu Cầu Phi Chức Năng (Non-Functional Requirements - NFRs)

| Nhóm Tiêu chuẩn | Chỉ số Mục tiêu | Tiêu chí Chấp thuận (Acceptance Criteria) | Phương pháp Đo lường |
|---|---|---|---|
| **Hiệu năng (Performance)** | Cache Hit: $< 50	ext{ ms}$<br>LLM Call: $< 2.5	ext{ s}$ | 95% câu hỏi trong cache phản hồi $< 50	ext{ ms}$; 90% lượt gọi LLM phản hồi $< 2.5	ext{ s}$. | Cloud Trace & FastAPI APM Metrics. |
| **Độ sẵn sàng (Availability)** | $\ge 99.9\%$ Uptime | Không có điểm lỗi đơn (No Single Point of Failure). Cloud Run tự động khởi tạo lại container lỗi. | Cloud Monitoring Uptime Checks. |
| **Khả năng mở rộng (Scalability)** | $0 	o 1,000	ext{ req/s}$ | Tự động scale out trên Cloud Run mà không bị nghẽn CPU hoặc cạn kiệt Connection Pool. | Locust Load Testing Suite. |
| **An toàn Thông tin (Security)** | Zero Trust & Least Privilege | 100% endpoint được bảo vệ bởi SSO Middleware. Quyền IAM Service Account được phân bổ tối thiểu. | Báo cáo Kiểm thử Thâm nhập & SAIF Audit. |
| **Khả năng phục hồi (Resilience)** | Graceful Degradation | Khi Firestore hoặc BigQuery gián đoạn trong Dev, tự động fallback an toàn. Trong Prod, kích hoạt Fail-Closed. | 3-Suite Pytest CI Matrix (361 test cases). |

---

## 6. Hành Trình Người Dùng & Kịch Bản Nghiệp Vụ (Use Cases)

### Use Case 01: Nhân viên yêu cầu Reset Mật khẩu Active Directory (L1)
1. **Diễn viên**: Nhân viên kinh doanh (Role: `employee`).
2. **Luồng sự kiện**:
   - **Bước 1**: Nhân viên gửi tin nhắn: *"Tôi bị khóa tài khoản máy tính do gõ sai pass nhiều lần, hỗ trợ reset giúp tôi"*.
   - **Bước 2**: Middleware xác thực token OIDC hợp lệ thuộc domain công ty.
   - **Bước 3**: Orchestrator nhận diện ý định và định tuyến đến `l1_selfservice_agent`.
   - **Bước 4**: L1 Agent hướng dẫn quy trình tự mở khóa qua cổng Self-Service Portal, đồng thời gọi `create_case` tạo case `CASE-AD-XXXX`.
   - **Bước 5**: Phản hồi hướng dẫn chi tiết và mã case cho nhân viên.

### Use Case 02: Kế toán viên báo lỗi Phân quyền Purchase Order trên SAP ERP (L2)
1. **Diễn viên**: Kế toán viên (Role: `employee`).
2. **Luồng sự kiện**:
   - **Bước 1**: Kế toán viên gửi: *"Tôi không thể duyệt đơn hàng PO-9981 trên SAP, hệ thống báo lỗi Authorization Check Failure M_EINK_FRG"*.
   - **Bước 2**: Orchestrator nhận diện từ khóa 'SAP', 'PO', 'Authorization' và chuyển tiếp cho `l2_enterprise_rag_agent`.
   - **Bước 3**: L2 Agent gọi `search_enterprise_knowledge` từ Enterprise RAG MCP để tra cứu cẩm nang xử lý lỗi ERP.
   - **Bước 4**: L2 Agent trích xuất giải pháp: cần cấp quyền qua transaction SU53 hoặc gửi yêu cầu phê duyệt cho Trưởng bộ phận Mua hàng, sau đó gọi `draft_email_response` soạn sẵn email mẫu gửi cho cấp quản lý.

### Use Case 03: Kỹ sư DevOps gửi Log Trace phân tích sự cố sập máy chủ (L3)
1. **Diễn viên**: Kỹ sư DevOps (Role: `devops_engineer` / `it_admin`).
2. **Luồng sự kiện**:
   - **Bước 1**: Kỹ sư gửi đoạn log stack trace máy chủ chứa các dòng *"java.lang.OutOfMemoryError: Java heap space"* và *"exit code 137"*.
   - **Bước 2**: Orchestrator nhận diện log kỹ thuật và chuyển tiếp cho `l3_deep_diagnostics_agent` (Gemini Pro).
   - **Bước 3**: L3 Agent gọi `analyze_system_logs_for_rca`. Hàm kiểm tra quyền RBAC: phát hiện user có role đặc quyền -> Cho phép thực thi.
   - **Bước 4**: Động cơ phân tích phát hiện mẫu `OUT_OF_MEMORY` và sinh báo cáo RCA chuẩn 4 phần (Hiện tượng, Nguyên nhân gốc rễ rò rỉ bộ nhớ, Workaround nâng RAM và Kế hoạch phòng ngừa Heap Dump Profiling).

### Use Case 04: Chuyên viên Pháp chế Rà soát Cam kết SLA Hợp đồng Đám mây (L3)
1. **Diễn viên**: Chuyên viên Pháp chế (Role: `compliance_officer` / `it_admin`).
2. **Luồng sự kiện**:
   - **Bước 1**: Chuyên viên gửi hợp đồng dịch vụ IT yêu cầu kiểm tra chỉ số SLA và rủi ro pháp lý.
   - **Bước 2**: `l3_deep_diagnostics_agent` gọi `review_it_contract_sla`.
   - **Bước 3**: Động cơ Regex bóc tách thành công: `'99.95% Uptime'`, `'Thời gian phản hồi trong vòng 30 phút'`.
   - **Bước 4**: Hệ thống cảnh báo rủi ro pháp lý cao do hợp đồng thiếu điều khoản bồi thường thiệt hại tài chính (Service Credits) và thiếu cam kết thông báo sự cố rò rỉ dữ liệu trong vòng 72 giờ (DPA).

---

## 7. Mô Hình Dữ Liệu & Hợp Đồng Giao Tiếp (Data Contracts)

Cấu trúc dữ liệu chính thức của hệ thống được chuẩn hóa bằng Pydantic Models:

```python
# Schema Case Lưu trữ Đa tầng (Firestore / In-Memory với OCC & History)
class Case(BaseModel):
    case_id: str = Field(description="Mã định danh duy nhất CASE-XXXXXXXX")
    user_id: str = Field(description="ID nhân viên yêu cầu")
    title: str = Field(description="Tiêu đề tóm tắt sự cố")
    description: str = Field(description="Mô tả chi tiết lỗi gặp phải")
    category: str = Field(default="General", description="Phân loại sự cố IT")
    priority: Literal["P1", "P2", "P3", "P4"] = "P3"
    status: Literal["Open", "In_Progress", "Escalated", "Pending", "Resolved", "Closed"] = "Open"
    assigned_tier: str = "L1_SelfService"
    resolution_notes: Optional[str] = None
    created_at: str
    updated_at: str
    version: int = Field(default=1, description="Optimistic Concurrency Control version")
    history: list[dict[str, Any]] = Field(default_factory=list, description="Append-only audit trail")

# Schema Người dùng Xác thực OIDC (SSOUser)
class SSOUser(BaseModel):
    user_id: str
    email: str
    email_verified: bool = True
    full_name: str = "Employee"
    department: str = "General"
    roles: list[str] = ["employee"]
    hosted_domain: Optional[str] = None
    is_authenticated: bool = True
    clearance_level: int = Field(default=0, ge=0, le=3)
```

---

## 8. Lộ Trình Phát Triển & Kế Hoạch Bàn Giao (Product Roadmap)

| Giai đoạn (Phase) | Mốc Thời gian | Mục tiêu Tính năng Chính | Trạng thái Hiện tại |
|---|---|---|---|
| **Phase 1: MVP Core** | Q1 / 2026 | Phân cấp 3-Tier Multi-Agent, Gemini integration, in-memory knowledge store. | **ĐÃ HOÀN THÀNH (100%)** |
| **Phase 2: Enterprise Ready** | Q2 / 2026 | Google OIDC SSO, Fail-Closed domain, BigQuery Vector Search, Semantic Cache, Terraform IaC. | **ĐÃ HOÀN THÀNH (100%)** |
| **Phase 3: Domain Packs & Resilience** | Q3 / 2026 | Domain Packs isolation (`_template` / `it-helpdesk`), Zero Hardcode, Redis Memorystore Auth/TLS, RAG Reranker Fallback, 361 CI Test Suites. | **ĐÃ HOÀN THÀNH (100%)** |
| **Phase 4: ITSM Integration** | Q4 / 2026 | Đồng bộ 2 chiều với ServiceNow, Jira Service Management, Slack/Teams Bot Connectors. | Kế hoạch Quý 4 |
| **Phase 5: Multimodal Voice** | Q1 / 2027 | Hỗ trợ cuộc gọi thoại trực tiếp qua Gemini Live Audio Streaming, OCR chụp màn hình lỗi. | Kế hoạch tương lai |

---

> [!NOTE]
> **Phê duyệt Sản phẩm**: Tài liệu Đặc tả Yêu cầu Sản phẩm & Nghiệp vụ (SRS) này đã được hoàn thiện, kiểm chứng thông qua bộ kiểm thử tự động **361 test cases trên 3 môi trường** và sẵn sàng phục vụ cho quá trình nghiệm thu, bàn giao sản phẩm.\n