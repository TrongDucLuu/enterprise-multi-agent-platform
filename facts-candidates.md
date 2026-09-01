# Báo cáo Kiểm kê Ứng viên Bảng Facts (L1 Kernel Registry)

> **Mục đích:** Liệt kê các giá trị cấu hình, ngưỡng số học, port, IP, version, SLA point-lookup đang nằm rải rác trong `config/systems.yaml` và văn bản xuôi (`ENTERPRISE_ARTICLES`).
> **Nguyên tắc phân loại:** Chỉ tách nội dung **deterministic, point-lookup, đắt khi sai**. KHÔNG atomize toàn bộ KB narrative. Dưới đây là danh sách ứng viên đề xuất để đưa vào bảng `it_helpdesk_kb.facts`.

---

## 1. Ứng viên từ Enterprise Knowledge Articles (Prose)

| Fact Key | Domain | Giá trị đề xuất | Kiểu | Đơn vị | Nguồn tài liệu | Ghi chú & Rationale | Phân loại |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `erp.po.sla_hours` | ERP | `2` | int | hours | `docs/erp_po_manual.md` (ERP-KB-001) | SLA xử lý cấp quyền ME21N sau khi có phê duyệt | Xác nhận L1 Fact |
| `erp.accounting.special_period_open_start_time` | ERP | `17:00` | string | time | `docs/erp_period_lock.md` (ERP-KB-002) | Giờ bắt đầu khung giờ mở kỳ phụ OB52 | Xác nhận L1 Fact |
| `erp.accounting.special_period_open_end_time` | ERP | `19:00` | string | time | `docs/erp_period_lock.md` (ERP-KB-002) | Giờ kết thúc khung giờ mở kỳ phụ OB52 | Xác nhận L1 Fact |
| `erp.accounting.exchange_rate_sync_time` | ERP | `08:30` | string | time | `docs/erp_exchange_rates.md` (ERP-KB-004) | Thời điểm cronjob fetch tỷ giá TCURR hàng ngày | Xác nhận L1 Fact |
| `hrm.biometric.server_ip` | HRM | `10.0.12.55` | string | ip | `docs/hrm_timesheet_sync.md` (HRM-KB-101) | Địa chỉ IP máy chủ dịch vụ HR-Biometric-Sync | Xác nhận L1 Fact |
| `hrm.timesheet.payroll_lock_day` | HRM | `25` | int | day | `docs/hrm_timesheet_sync.md` (HRM-KB-101) | Ngày khóa bảng công tháng tự động | Xác nhận L1 Fact |
| `hrm.onboarding.data_lead_days` | HRM | `3` | int | days | `docs/hrm_onboarding.md` (HRM-KB-102) | Thời hạn nhập hồ sơ nhân sự trước ngày onboard | Xác nhận L1 Fact |
| `hrm.onboarding.sync_cron_time` | HRM | `00:00` | string | time | `docs/hrm_onboarding.md` (HRM-KB-102) | Giờ chạy job đồng bộ tài khoản Active Directory | Xác nhận L1 Fact |
| `hrm.annual_leave.rollover_max_days` | HRM | `5` | int | days | `docs/hrm_annual_leave.md` (HRM-KB-103) | Số ngày phép dư tối đa được chuyển sang Q1 năm sau | Xác nhận L1 Fact |
| `hrm.annual_leave.manager_approval_max_days` | HRM | `2` | int | days | `docs/hrm_annual_leave.md` (HRM-KB-103) | Số ngày nghỉ tối đa Quản lý trực tiếp được duyệt | Xác nhận L1 Fact |
| `hrm.annual_leave.director_approval_min_days` | HRM | `3` | int | days | `docs/hrm_annual_leave.md` (HRM-KB-103) | Số ngày nghỉ tối thiểu cần Giám đốc Khối duyệt | Xác nhận L1 Fact |
| `hrm.tax.dependent_filing_day` | HRM | `15` | int | day | `docs/hrm_tax_dependents.md` (HRM-KB-104) | Ngày C&B nộp hồ sơ giảm trừ gia cảnh thuế TNCN | Xác nhận L1 Fact |
| `hrm.offboarding.ticket_lead_days` | HRM | `7` | int | days | `docs/hrm_offboarding_process.md` (HRM-KB-106) | Thời hạn tạo ticket offboarding trước ngày nghỉ | Xác nhận L1 Fact |
| `hrm.offboarding.account_lock_time` | HRM | `18:00` | string | time | `docs/hrm_offboarding_process.md` (HRM-KB-106) | Giờ tự động khóa tài khoản SSO/Email ngày nghỉ | Xác nhận L1 Fact |
| `hrm.performance_review.self_assessment_deadline_day` | HRM | `20` | int | day | `docs/hrm_performance_review.md` (HRM-KB-107) | Hạn hoàn thành bản Tự đánh giá KPI/OKR cuối quý | Xác nhận L1 Fact |
| `hrm.shift.night_shift_start_time` | HRM | `22:00` | string | time | `docs/hrm_shift_scheduling.md` (HRM-KB-108) | Giờ bắt đầu ca đêm | Xác nhận L1 Fact |
| `hrm.shift.night_shift_end_time` | HRM | `06:00` | string | time | `docs/hrm_shift_scheduling.md` (HRM-KB-108) | Giờ kết thúc ca đêm | Xác nhận L1 Fact |
| `hrm.shift.night_shift_allowance_pct` | HRM | `30` | int | % | `docs/hrm_shift_scheduling.md` (HRM-KB-108) | Tỷ lệ phụ cấp lương làm việc ca đêm theo luật | Xác nhận L1 Fact |
| `hrm.social_insurance.d02_filing_deadline_day` | HRM | `20` | int | day | `docs/hrm_social_insurance.md` (HRM-KB-109) | Hạn nộp mẫu D02-LT cho cơ quan BHXH hàng tháng | Xác nhận L1 Fact |
| `hrm.travel_expense.vat_invoice_deadline_days` | HRM | `5` | int | days | `docs/hrm_travel_expense.md` (HRM-KB-110) | Hạn upload hóa đơn VAT sau khi đi công tác về | Xác nhận L1 Fact |
| `crm.api.daily_limit_alert_threshold_pct` | CRM | `90` | int | % | `docs/crm_lead_sync.md` (CRM-KB-201) | Ngưỡng cảnh báo giới hạn gọi API Daily Limit | Xác nhận L1 Fact |
| `crm.quote.discount_auto_approve_max_pct` | CRM | `10` | int | % | `docs/crm_quote_template.md` (CRM-KB-203) | Mức chiết khấu Quote tối đa được hệ thống tự duyệt | Xác nhận L1 Fact |
| `crm.quote.discount_sales_director_max_pct` | CRM | `20` | int | % | `docs/crm_quote_template.md` (CRM-KB-203) | Mức chiết khấu Quote cần Sales Director duyệt | Xác nhận L1 Fact |
| `crm.data_quality.merge_contacts_max_count` | CRM | `3` | int | records | `docs/crm_duplicate_rules.md` (CRM-KB-204) | Số lượng bản ghi Contact trùng tối đa cho phép gộp | Xác nhận L1 Fact |
| `crm.reporting.dashboard_refresh_time` | CRM | `08:00` | string | time | `docs/crm_dashboard_reports.md` (CRM-KB-206) | Thời gian tự động gửi email báo cáo sáng thứ Hai | Xác nhận L1 Fact |
| `crm.marketing.dkim_key_size_bits` | CRM | `2048` | int | bits | `docs/crm_email_deliverability.md` (CRM-KB-209) | Chuẩn độ dài khóa mã hóa DKIM Marketing Cloud | Xác nhận L1 Fact |
| `crm.support.high_critical_case_response_sla_minutes` | CRM | `15` | int | minutes | `docs/crm_omnichannel_routing.md` (CRM-KB-210) | SLA cảnh báo Case High/Critical chưa phản hồi | Xác nhận L1 Fact |

---

## 2. Ứng viên từ Configuration Systems YAML (`config/systems.yaml`)

| Fact Key | Domain | Giá trị đề xuất | Kiểu | Đơn vị | Nguồn cấu hình | Ghi chú & Rationale | Phân loại |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `pipeline.chunking.max_chunk_size` | SYSTEM | `1200` | int | chars | `config/systems.yaml` | Cỡ chunk tối đa của pipeline | Xác nhận L1 Fact |
| `pipeline.chunking.overlap` | SYSTEM | `150` | int | chars | `config/systems.yaml` | Độ dài overlap giữa các chunk | Xác nhận L1 Fact |
| `pipeline.chunking.well_structured_max_section_ratio` | SYSTEM | `0.65` | float | ratio | `config/systems.yaml` | Tỷ lệ tối đa của section so với toàn tài liệu | Xác nhận L1 Fact |
| `pipeline.chunking.well_structured_min_avg_section_length` | SYSTEM | `100` | int | chars | `config/systems.yaml` | Độ dài trung bình tối thiểu của section | Xác nhận L1 Fact |
| `pipeline.document_processing.document_ai_timeout_seconds` | SYSTEM | `60` | int | seconds | `config/systems.yaml` | Timeout gọi Document AI | Xác nhận L1 Fact |
| `pipeline.document_processing.document_ai_max_retries` | SYSTEM | `2` | int | count | `config/systems.yaml` | Số lần thử lại tối đa Document AI | Xác nhận L1 Fact |
| `pipeline.retrieval.fraction_lists_to_search` | SYSTEM | `0.05` | float | ratio | `config/systems.yaml` | Tỷ lệ IVF list tìm kiếm trong vector search | Xác nhận L1 Fact |

---

## 3. Danh sách Chưa phân loại / Giữ nguyên Vector Store (SOP & Narrative)

Các tài liệu và quy trình sau KHÔNG được tách sang Facts vì bản chất là chuỗi bước xử lý nghiệp vụ (SOP narrative):
- `ERP-KB-003`: Quy trình kiểm kê tồn kho và in phiếu xuất kho (T-code MB52/MIGO).
- `ERP-KB-005`: Quy trình tạo và duyệt Vendor Master Data (T-code BP/XK01).
- `ERP-KB-006`: Lập hóa đơn bán hàng và hạch toán doanh thu (VF01/VF04).
- `ERP-KB-007`: Khắc phục lỗi batch job hạch toán lương cuối tháng (PCP0/KS03).
- `ERP-KB-008`: Quy trình phê duyệt PR to PO (ME51N/ME21N).
- `ERP-KB-009`: Báo cáo phân tích ngân sách dự án (CJ20N).
- `ERP-KB-010`: Hướng dẫn kết chuyển số dư tài chính cuối năm (F.07/F.16).
- `HRM-KB-105`: Khắc phục sự cố truy cập phiếu lương Payslip trên app mobile.
- `CRM-KB-202`: Quy trình Mass Transfer Records & phân quyền Territory.
- `CRM-KB-205`: Thiết lập Campaign Marketing và theo dõi ROI.
- `CRM-KB-207`: Tích hợp tổng đài VoIP CTI Screen Pop.
- `CRM-KB-208`: Ký hợp đồng điện tử qua DocuSign trên Salesforce.
