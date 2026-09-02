# TÀI LIỆU ĐẶC TẢ YÊU CẦU SẢN PHẨM & PHẦN MỀM (PRODUCT REQUIREMENTS & SOFTWARE SPECIFICATION - SRS)
## Nền Tảng Trợ Lý Trí Tuệ Nhân Tạo Đa Tác Tử Doanh Nghiệp (Enterprise Multi-Agent Platform - `agent_core`)

| Thuộc tính | Giá trị | Thuộc tính | Giá trị |
|---|---|---|---|
| **Tên Sản phẩm** | Enterprise Multi-Agent AI Platform | **Phiên bản SRS** | `2.2.0-Enterprise` |
| **Loại Tài liệu** | Product Spec & Functional SRS | **Chủ quản Sản phẩm** | Lead PM & Senior Enterprise BA |
| **Đối tượng Áp dụng** | Toàn bộ Khối Vận hành Doanh nghiệp (IT, HR, Legal, Ops, Finance) | **Kiến trúc Nền tảng** | Google ADK, Gemini 2.5 / 3 & Domain Packs |
| **Trạng thái Phê duyệt** | Approved for Production | **Ngày ban hành** | 02/09/2026 |

---

## 1. Tổng Quan Sản Phẩm & Tầm Nhìn Chiến Lược (Product Overview)

### 1.1. Bối cảnh & Thách thức Doanh nghiệp (Business Problem Statement)
Tại các tổ chức và tập đoàn quy mô lớn, khối vận hành nội bộ (Internal Operations - bao gồm IT Support, Nhân sự HR, Pháp chế Legal, Chăm sóc Khách hàng Customer Ops, và Dịch vụ Tài chính Finance) đối mặt với những thách thức vận hành nghiêm trọng:
- **60% – 70% yêu cầu nội bộ là các tác vụ lặp lại cơ bản** (tra cứu quy trình, hướng dẫn tự phục vụ, hỏi đáp chính sách, tạo yêu cầu hỗ trợ).
- **Thời gian phản hồi ban đầu (FRT) và thời gian giải quyết yêu cầu (MTTR) kéo dài** từ 4 đến 24 giờ do phụ thuộc hoàn toàn vào nhân sự trực ca thủ công.
- **Kho tài liệu nghiệp vụ khổng lồ nhưng phân tán** trên nhiều hệ thống (ERP, CRM, HRM, Wikis, Hợp đồng, Quy định), gây mất thời gian tìm kiếm và tăng tỷ lệ sai sót.
- **Rủi ro lộ lọt dữ liệu và vi phạm quyền riêng tư** khi nhân viên sử dụng các công cụ AI công cộng không có cơ chế phân quyền bảo mật (Clearance Levels) và cách ly dữ liệu.
- **Chi phí vận hành và nhân sự hỗ trợ gia tăng tuyến tính** theo tốc độ mở rộng quy mô của doanh nghiệp.

### 1.2. Tầm nhìn Sản phẩm (Product Vision)
Xây dựng một nền tảng **Trợ lý AI Đa Tác tử Tự chủ, Thông minh và An toàn Cấp Doanh nghiệp (Enterprise Multi-Agent Platform - `agent_core`)**, đóng vai trò là **Điểm Tiếp Nhận Duy Nhất (Single Point of Contact - SPOC)** cho mọi hoạt động vận hành nội bộ, có khả năng:
1. **Tiếp nhận & Phân luồng Tự động (Dynamic Root Triage)**: Hiểu ngôn ngữ tự nhiên, phân tích ý định và chuyển giao tác vụ chính xác cho các Sub-Agent chuyên trách.
2. **Tự phục vụ Tức thì (Tier-1 Self-Service)**: Giải quyết tức thì các yêu cầu cơ bản, tra cứu Fact xác định tuyệt đối (Deterministic Facts) và quản lý vòng đời Case/Ticket tự động.
3. **Tra cứu Tri thức Sâu (Tier-2 Enterprise RAG)**: Nắm vững toàn bộ tài liệu nghiệp vụ nội bộ thông qua BigQuery Vector Search kết hợp Re-ranker thông minh và cơ chế Fallback chống gián đoạn.
4. **Chẩn đoán & Phân tích Chuyên gia (Tier-3 Deep Diagnostics)**: Hỗ trợ các tác vụ phân tích chuyên sâu (phân tích log lỗi hạ tầng RCA, rà soát nghĩa vụ hợp đồng SLA/DPA, kiểm toán tuân thủ).
5. **Mở rộng Đa Miền Nghiệp vụ Linh hoạt (Decoupled Domain Packs)**: Tách rời 100% Core Platform khỏi nghiệp vụ, cho phép doanh nghiệp kích hoạt hoặc tạo mới các gói nghiệp vụ (IT Helpdesk, HR Service, Legal Compliance, Customer Support) chỉ qua cấu hình declarative YAML.

### 1.3. Mục tiêu Nghiệp vụ & Chỉ số Đánh giá Hiệu quả (Business Goals & OKRs/KPIs)

| Chỉ số Đo lường (KPI) | Trước khi áp dụng | Mục tiêu sau triển khai | Ý nghĩa Nghiệp vụ & Đóng góp |
|---|---|---|---|
| **Tỷ lệ Tự phục vụ Thành công (FCR)** | < 15% | $\ge 65\%$ | Người dùng tự xử lý thành công ngay ở Mức 1 mà không cần can thiệp con người. |
| **Thời gian Giải quyết Yêu cầu (MTTR)** | 4.5 giờ | $< 15$ phút (Trung bình) | Giảm thiểu thời gian gián đoạn công việc của nhân viên và khách hàng. |
| **Tỷ lệ Phản hồi Tức thì (Cache Hit)** | 0% | $\ge 50\%$ câu hỏi lặp | Lớp Semantic Cache phản hồi $< 25	ext{ ms}$, tiết kiệm 100% token LLM. |
| **Độ chính xác Phân tích Chuyên sâu** | Thủ công (1–2 ngày) | Tức thì ($< 30$ giây) | Bóc tách chính xác nguyên nhân lỗi hoặc điều khoản vi phạm SLA/DPA. |
| **Chỉ số Hài lòng Người dùng (CSAT)** | 3.2 / 5.0 | $\ge 4.7 / 5.0$ | Trải nghiệm hội thoại tự nhiên, thông minh, phục vụ 24/7/365. |

---

## 2. Chân Dung Người Dùng & Bên Liên Quan (User Personas)

| Chân dung (Persona) | Vai trò Doanh nghiệp | Nhu cầu & Nỗi đau chính | Kỳ vọng đối với Nền tảng AI |
|---|---|---|---|
| **Người Yêu cầu (Enterprise Requester)** | Nhân viên / Khách hàng | Cần giải đáp chính sách, hỗ trợ thủ tục, gặp sự cố cần giải quyết gấp. | Nhận hướng dẫn chi tiết từng bước, tự xử lý tức thì, tạo case hỗ trợ tự động. |
| **Chuyên viên Vận hành Mức 1–2 (Tier 1-2 Ops)** | Chuyên viên Hỗ trợ Nghiệp vụ | Quá tải các yêu cầu lặp lại, mất thời gian tra cứu cẩm nang nghiệp vụ dài. | AI tự động phân loại, tóm tắt quy trình chuyên sâu và soạn sẵn phản hồi chuẩn hóa. |
| **Chuyên gia / Kỹ sư Cấp cao (Tier-3 SME / SRE)** | Chuyên gia Kỹ thuật / Pháp chế | Áp lực phân tích sự cố phức tạp, rà soát hợp đồng pháp lý / SLA thủ công. | Bóc tách dữ liệu tự động, khoanh vùng nguyên nhân gốc rễ và cảnh báo vi phạm cam kết. |
| **Chuyên viên Tuân thủ & Bảo mật (CISO / Legal)** | Giám sát Rủi ro & Pháp lý | Rủi ro lộ lọt bí mật kinh doanh, vi phạm quy định GDPR / ISO 27001. | Phân quyền 4 cấp độ Clearance ($0 \dots 3$), bảo vệ Zero-Trust, nhật ký kiểm toán bất biến. |
| **Lãnh đạo Chuyển đổi Số (CIO / CTO / Ops Lead)**| Lãnh đạo Chiến lược | Chi phí vận hành cao, khó mở rộng sang nhiều phòng ban, vendor lock-in. | Nền tảng kiến trúc mở (Domain Packs), triển khai độc lập (GCP Project Isolation), chi phí $0 khi rảnh rỗi. |

---

## 3. Phạm Vi Sản Phẩm & Ràng Buộc Nghiệp Vụ (Scope & Constraints)

- **Phạm vi Nền tảng (In-Scope Platform Capabilities)**:
  - Tiếp nhận và phân luồng tự động yêu cầu đa miền qua giao tiếp ngôn ngữ tự nhiên.
  - Hướng dẫn tự phục vụ và tra cứu Fact xác định tuyệt đối (Deterministic Facts Registry).
  - Tra cứu tri thức nghiệp vụ sâu qua Enterprise RAG MCP với cơ chế Cross-Encoder Re-ranker và Circuit Breaker.
  - Phân tích dữ liệu chuyên sâu (chẩn đoán lỗi kỹ thuật RCA, rà soát cam kết SLA/DPA pháp lý).
  - Quản lý phân cấp bảo mật tri thức 4 tầng (**Clearance Levels: Public, Internal, Confidential, Restricted**).
  - Xác thực tập trung Google OIDC với cơ chế Fail-Closed Domain Filtering và phân quyền RBAC.
  - Quản lý vòng đời Case/Ticket với Cloud Firestore, Optimistic Concurrency Control (OCC) và Append-Only Audit Trail.
  - Hỗ trợ kiến trúc Domain Packs đa lĩnh vực (`it-helpdesk`, `hr-service`, `legal-compliance`, `_template`).

- **Phạm vi Ngoài Sản phẩm (Out-of-Scope)**:
  - Tự ý thực thi các quyết định tài chính hoặc phê duyệt chi ngân sách vượt thẩm quyền mà không có xác nhận của con người (Human-in-the-loop).

---

## 4. Đặc Tả Yêu Cầu Chức Năng Nền Tảng (Functional Requirements - FRs)

### FR-01: Tầng Tiếp Nhận & Điều Phối Đa Tác Tử Động (Dynamic Root Intent Triage)
- **Mã chức năng**: `FR-01-TRIAGE`
- **Tác tử thực thi**: `root_orchestrator` (Gemini 2.5 / Flash Preview).
- **Yêu cầu chi tiết**:
  1. Tiếp nhận câu hỏi và ngữ cảnh người dùng qua giao diện đàm thoại.
  2. Tự động nhận diện ý định (Intent Recognition) và phân loại định tuyến dựa trên cấu hình khai báo từ active **Domain Pack**:
     - Định tuyến đến Tier-1: Câu hỏi FAQ, quy trình tự phục vụ, yêu cầu tạo case cơ bản.
     - Định tuyến đến Tier-2: Yêu cầu tra cứu tri thức nghiệp vụ chuyên sâu, cẩm nang quy trình, hướng dẫn vận hành.
     - Định tuyến đến Tier-3: Yêu cầu phân tích dữ liệu chuyên sâu, chẩn đoán nguyên nhân lỗi, hoặc rà soát cam kết pháp lý.
  3. Tổng hợp kết quả phản hồi từ các Sub-agent và trả về cho người dùng với văn phong chuyên nghiệp, chuẩn mực.

### FR-02: Mức 1 - Tự Phục Vụ & Quản Lý Case/Ticket (Tier-1 Self-Service & Case Lifecycle)
- **Mã chức năng**: `FR-02-SELFSERVICE`
- **Tác tử thực thi**: Tier-1 Specialist Agent (Gemini Flash).
- **Yêu cầu chi tiết**:
  1. Hướng dẫn chi tiết từng bước (Step-by-step) các quy trình tự phục vụ được khai báo trong Domain Pack.
  2. Tra cứu tức thì các thông số định mức cứng qua công cụ `lookup_fact` (Facts Registry), triệt tiêu ảo giác 100%.
  3. Quản lý Case/Ticket tự động: Sử dụng công cụ `create_case` tạo case mới với ID duy nhất (`CASE-XXXXXXXX`), thiết lập độ ưu tiên (`P1`..`P4`) và ghi nhận vào Firestore với OCC versioning.
  4. Cung cấp chức năng tra cứu lịch sử case cá nhân (`list_user_cases`) và chi tiết case (`get_case`).

### FR-03: Mức 2 - Tra Cứu Tri Thức Nghiệp Vụ & Enterprise RAG (Tier-2 Enterprise RAG)
- **Mã chức năng**: `FR-03-ENTERPRISE-RAG`
- **Tác tử thực thi**: Tier-2 Specialist Agent kết hợp Enterprise RAG MCP.
- **Yêu cầu chi tiết**:
  1. Tra cứu sâu cơ sở tri thức nghiệp vụ qua MCP Tools (`search_enterprise_knowledge`, `get_system_manual`):
     - Truy vấn BigQuery Vector Search kết hợp SQL pre-filtering theo `clearance_level <= @user_clearance`.
     - Tự động re-rank tài liệu bằng Cross-Encoder model. Nếu model quá tải, tự động kích hoạt Circuit Breaker fallback về Cosine/BM25.
  2. Đính kèm trích dẫn nguồn tài liệu chuẩn xác (`source`, `chunk_id`, `updated_at`).
  3. Hỗ trợ công cụ soạn thảo thông báo và email phản hồi chuẩn hóa (`draft_email_response`).

### FR-04: Mức 3 - Chẩn Đoán Chuyên Gia & Cam Kết Pháp Lý (Tier-3 Deep Diagnostics & Obligations)
- **Mã chức năng**: `FR-04-DEEP-DIAGNOSTICS`
- **Tác tử thực thi**: Tier-3 Specialist Agent (Gemini 2.5 Pro - High Reasoning CoT).
- **Yêu cầu chi tiết**:
  1. Thực thi các plugin chẩn đoán chuyên sâu (ví dụ: bóc tách log lỗi hệ thống, phân loại 6 nhóm lỗi cốt lõi: OOM, DB, Disk, Null, Auth, Network).
  2. Rà soát cam kết pháp lý và thỏa thuận SLA qua `get_obligation`, `list_contract_obligations`, phát hiện nguy cơ vi phạm bồi thường.
  3. Áp dụng giới hạn điều tốc nghiêm ngặt (10 req/phút / user) để bảo vệ quota mô hình suy luận cao cấp.

### FR-05: Bộ Đệm Ngữ Nghĩa Phân Vùng Bảo Mật (Clearance-Aware Semantic Cache)
- **Mã chức năng**: `FR-05-SEMANTIC-CACHE`
- **Cơ chế thực thi**: Redis Memorystore / In-Memory Vector Matching.
- **Yêu cầu chi tiết**:
  1. Nhận diện `caller_clearance` của người gọi từ `runtime.py` và phân vùng cache namespace (`_c0..c3_`).
  2. Áp dụng First-Turn Gating: Chỉ cho phép lưu vào Public Cache khi `turn_count <= 1`, không gọi tool nhạy cảm và `clearance_level == 0`. Mọi câu hỏi có clearance > 0 bắt buộc lưu vào User Private Cache.
  3. Quét vector ứng viên và tính Cosine Similarity: Nếu $\ge 0.92$, trả kết quả tức thì ($< 25	ext{ms}$), tiết kiệm 100% chi phí gọi LLM.

### FR-06: Xác Thực Tập Trung Zero-Trust & Quản Lý Đồng Thời OCC (Security & Concurrency)
- **Mã chức năng**: `FR-06-SECURITY-CONCURRENCY`
- **Yêu cầu chi tiết**:
  1. Xác thực Google OIDC JWKS token trên mọi API request, áp dụng bộ lọc tên miền `ALLOWED_DOMAINS` chế độ **Fail-Closed**.
  2. Kiểm soát phân quyền IDOR: Người dùng chỉ được xem/sửa case của chính mình; chỉ vai trò quản trị (`admin_roles`) mới được xem toàn bộ.
  3. Quản lý trạng thái Case với Optimistic Concurrency Control (`version`), ném lỗi `CaseConcurrencyConflictError` khi phát hiện xung đột và ghi nhận nhật ký `history` bất biến.

---

## 5. Đặc Tả Yêu Cầu Phi Chức Năng (Non-Functional Requirements - NFRs)

| Mã NFR | Phân loại | Tiêu chuẩn Kỹ thuật & Chỉ số Đo lường |
|---|---|---|
| **NFR-01** | **Độ trễ Phản hồi (Latency)** | Cache Hit: $\le 25	ext{ ms}$; Tier-1/Tier-2 Agent: $\le 3.5	ext{ giây}$ ($p95$); Tier-3 Deep Reasoning: $\le 15	ext{ giây}$. |
| **NFR-02** | **Khả năng Mở rộng (Scalability)** | Hỗ trợ tải đồng thời tối thiểu **100 CCU** trên 1 Cloud Run instance, tự động autoscaling lên 10+ instances khi lưu lượng tăng. |
| **NFR-03** | **Độ Sẵn Sàng (Availability)** | Cam kết Uptime SLA $\ge 99.9\%$, hỗ trợ Zero-Downtime Blue/Green Rollout qua Cloud Run Revisions. |
| **NFR-04** | **An Toàn Thông Tin (Security)** | Zero-Trust IAM, mã hóa TLS 1.3 In-Transit, AES-256 At-Rest, cách ly tuyệt đối 1 GCP Project / Khách hàng. |
| **NFR-05** | **Khả năng Mở rộng Nghiệp vụ (Extensibility)** | Cho phép tạo và nạp Domain Pack mới trong $< 1$ giờ mà không cần biên dịch lại mã nguồn Core (`agent_core/`). |

---

## 6. Lộ Trình Phát Triển Sản Phẩm (Product Roadmap)

- **Giai đoạn 1 (v2.0.0 - Đã hoàn thành)**: Xây dựng nền tảng Core Engine, Dynamic Agent Builder, BigQuery Vector Search, SSO OIDC và IT Helpdesk Reference Pack.
- **Giai đoạn 2 (v2.2.0 - Hiện tại)**: Chuẩn hóa Domain Packs, Clearance-Aware Semantic Cache, Optimistic Concurrency Control (OCC) Case Store, Redis TLS/Auth với Secret Manager, và 3-Suite CI Protocol (361 test cases).
- **Giai đoạn 3 (v2.3.0 - Quý 4/2026)**: Ra mắt các Domain Packs chính thức: `hr-service`, `legal-compliance`, `customer-ops`; bổ sung giao diện quản trị No-Code Domain Pack Studio.
