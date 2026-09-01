# L3 Obligations Kernel Registry — Candidate Audit & Schema

> **Document Version:** 1.0.0  
> **Target Schema:** `it_helpdesk_kb.obligations`  
> **Classification Principle:** Tách riêng các nghĩa vụ pháp lý, điều khoản hợp đồng và cam kết SLA mang tính **ràng buộc, định lượng và có chế tài xử phạt (deterministic & legally binding)**.

---

## 1. BigQuery Table Schema: `it_helpdesk_kb.obligations`

```sql
CREATE TABLE IF NOT EXISTS `{project}.{dataset}.obligations` (
  obligation_id STRING NOT NULL,          -- e.g. 'OBL-SAP-001'
  source_id STRING NOT NULL,              -- e.g. 'CONTRACT-SAP-ENTERPRISE-2024'
  source_title STRING NOT NULL,           -- e.g. 'SAP Enterprise Support Agreement'
  authority STRING NOT NULL,              -- e.g. 'Legal Counsel', 'VP of IT', 'CISO'
  article STRING,                         -- e.g. 'Section 3.1', 'Clause 4.2'
  description STRING NOT NULL,            -- Nội dung cam kết chuẩn mực
  severity STRING NOT NULL,               -- 'critical' | 'high' | 'medium' | 'low'
  applies_to STRING NOT NULL,             -- 'vendor' | 'customer' | 'both'
  date_added DATE NOT NULL,
  date_effective DATE NOT NULL,
  date_expires DATE,
  status STRING NOT NULL,                 -- 'active' | 'superseded' | 'expired'
  source_document_path STRING             -- Path hoặc URI văn bản gốc
);
```

---

## 2. Seed Obligations Registry (16 Audited Candidates)

| Obligation ID | Source ID | Source Title | Authority | Article | Severity | Applies To | Description | Status |
|---|---|---|---|---|---|---|---|---|
| `OBL-SAP-001` | `CONTRACT-SAP-ENTERPRISE-2024` | SAP Enterprise Support Agreement | VP of IT | Section 3.1 | `critical` | `vendor` | Cam kết Uptime hệ thống tối thiểu 99.95% mỗi tháng theo lịch 24/7. | `active` |
| `OBL-SAP-002` | `CONTRACT-SAP-ENTERPRISE-2024` | SAP Enterprise Support Agreement | VP of IT | Section 4.1 | `high` | `vendor` | Thời gian phản hồi sự cố khẩn cấp (P1) trong vòng tối đa 30 phút. | `active` |
| `OBL-SAP-003` | `CONTRACT-SAP-ENTERPRISE-2024` | SAP Enterprise Support Agreement | VP of IT | Section 4.2 | `critical` | `vendor` | Thời gian khắc phục sự cố khẩn cấp (P1 MTTR) tối đa trong vòng 4 giờ. | `active` |
| `OBL-SAP-004` | `CONTRACT-SAP-ENTERPRISE-2024` | SAP Enterprise Support Agreement | Legal Counsel | Section 5.1 | `high` | `vendor` | Khấu trừ 10% Service Credits vào phí dịch vụ tháng tiếp theo nếu Uptime dưới 99.9%. | `active` |
| `OBL-SAP-005` | `CONTRACT-SAP-ENTERPRISE-2024` | SAP Enterprise Support Agreement | CISO | Section 7.2 | `critical` | `vendor` | Nhà cung cấp có trách nhiệm thông báo vi phạm dữ liệu (Data Breach) trong vòng 24 giờ kể từ khi phát hiện. | `active` |
| `OBL-SAP-006` | `CONTRACT-SAP-ENTERPRISE-2024` | SAP Enterprise Support Agreement | Legal Counsel | Section 8.1 | `medium` | `both` | Khách hàng có quyền thực hiện kiểm toán an toàn thông tin độc lập định kỳ hàng năm. | `active` |
| `OBL-SAP-007` | `CONTRACT-SAP-ENTERPRISE-2024` | SAP Enterprise Support Agreement | Legal Counsel | Section 9.1 | `high` | `both` | Tuân thủ thỏa thuận bảo mật thông tin vô điều kiện (Non-Disclosure Agreement - NDA). | `active` |
| `OBL-ORA-001` | `CONTRACT-ORACLE-CLOUD-2024` | Oracle Cloud Infrastructure Agreement | VP of IT | Schedule A | `high` | `vendor` | Cam kết tính sẵn sàng của cơ sở dữ liệu (Database Availability) tối thiểu 99.9% mỗi tháng. | `active` |
| `OBL-ORA-002` | `CONTRACT-ORACLE-CLOUD-2024` | Oracle Cloud Infrastructure Agreement | IT Operations | Schedule B.1 | `medium` | `vendor` | Thời gian phản hồi sự cố mức độ nghiêm trọng P2 trong vòng tối đa 2 giờ. | `active` |
| `OBL-ORA-003` | `CONTRACT-ORACLE-CLOUD-2024` | Oracle Cloud Infrastructure Agreement | IT Operations | Schedule B.2 | `medium` | `vendor` | Thời gian giải quyết sự cố P2 (Resolve Time) trong vòng tối đa 12 giờ. | `active` |
| `OBL-DPA-001` | `POLICY-DATA-PROTECTION-DPA-2024` | Enterprise Data Protection Addendum | DPO | Clause 6.1 | `critical` | `vendor` | Nghiêm cấm nhà cung cấp và nhà thầu phụ chuyển giao dữ liệu cá nhân của khách hàng ra ngoài khu vực lưu trữ đã thỏa thuận khi chưa có văn bản chấp thuận trước. | `active` |
| `OBL-DPA-002` | `POLICY-DATA-PROTECTION-DPA-2024` | Enterprise Data Protection Addendum | DPO | Clause 11.3 | `high` | `vendor` | Nhà cung cấp phải tiêu hủy an toàn hoặc hoàn trả toàn bộ dữ liệu định danh (PII) trong vòng 30 ngày kể từ khi chấm dứt hợp đồng. | `active` |
| `OBL-SEC-001` | `POLICY-INTERNAL-IT-SECURITY-2024` | IT Information Security Policy | CISO | SecPolicy 2.1 | `critical` | `customer` | Mọi tài khoản quản trị viên truy cập hệ thống doanh nghiệp cốt lõi bắt buộc phải kích hoạt xác thực đa yếu tố chống lừa đảo (FIDO2/Hardware MFA). | `active` |
| `OBL-SEC-002` | `POLICY-INTERNAL-IT-SECURITY-2024` | IT Information Security Policy | IT Security | SecPolicy 4.3 | `high` | `customer` | Quản lý bộ phận phải tái xét duyệt (Recertification) quyền truy cập đặc quyền của nhân viên định kỳ mỗi 90 ngày. | `active` |
| `OBL-SF-001` | `CONTRACT-SALESFORCE-CRM-2024` | Salesforce Master Subscription Agreement | VP of IT | Section 2.4 | `high` | `vendor` | Đảm bảo Uptime hệ thống CRM đạt tối thiểu 99.9% không bao gồm thời gian bảo trì định kỳ có thông báo trước 48h. | `active` |
| `OBL-WD-001` | `CONTRACT-WORKDAY-HRM-2024` | Workday Subscription Agreement | Head of HR | Exhibit C | `critical` | `vendor` | Khắc phục sự cố tắc nghẽn đồng bộ bảng lương trong vòng 2 giờ trước hạn chót khóa lương ngày 25 hàng tháng. | `active` |

---

## 3. RBAC Enforcement Matrix

| Functionality | Allowed Roles | Disallowed Roles (Access Denied) |
|---|---|---|
| `get_obligation(id)` | `compliance_officer`, `legal_counsel`, `it_admin`, `sys_admin` | `employee`, `hr_specialist`, `sales_rep`, `accountant` |
| `list_obligations(...)` | `compliance_officer`, `legal_counsel`, `it_admin`, `sys_admin` | `employee`, `hr_specialist`, `sales_rep`, `accountant` |
| `review_it_contract_sla(...)` | `compliance_officer`, `legal_counsel`, `it_admin`, `sys_admin` | `employee`, `hr_specialist`, `sales_rep`, `accountant` |
