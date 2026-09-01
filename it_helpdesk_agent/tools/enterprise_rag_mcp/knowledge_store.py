import os
import re
import math
import html
import time
import datetime
import logging
from abc import ABC, abstractmethod
from typing import Optional, Any

logger = logging.getLogger(__name__)


def _extract_str(val: Any) -> Optional[str]:
    """Safely extracts string representation from a BigQuery row field or model attribute."""
    if val is None:
        return None
    if isinstance(val, (datetime.date, datetime.datetime)):
        return val.isoformat()
    return str(val)


def _extract_bool(val: Any) -> bool:
    """Safely extracts boolean value."""
    if isinstance(val, bool):
        return val
    return bool(val) if val is not None else False


def _extract_list(val: Any) -> list[str]:
    """Safely extracts list of strings from BigQuery REPEATED fields."""
    if val is None:
        return []
    if isinstance(val, (list, tuple, set)):
        return [str(x) for x in val if x is not None]
    return [str(val)]


def escape_xml_attribute(val: Any) -> str:
    """
    Escapes special XML characters for attribute values (", <, >, &, ').
    Prevents attribute breakout and XML delimiter corruption.
    """
    if val is None:
        return ""
    return html.escape(str(val), quote=True)


def sanitize_retrieved_content(content: str) -> str:
    """
    Sanitizes raw document content to prevent delimiter injection attacks
    (e.g., embedding fake </retrieved_document> tags to break out of passive data boundary).
    Replaces any retrieved_document tag variations (case-insensitive, whitespace tolerant)
    with safe XML entity representations (&lt;...&gt;).
    """
    if not content:
        return ""
    return re.sub(
        r"<\s*(/)?\s*retrieved_document\b([^>]*)>",
        lambda m: f"&lt;{m.group(1) or ''}retrieved_document{m.group(2)}&gt;",
        content,
        flags=re.IGNORECASE
    )


def wrap_retrieved_document(content: str, doc_id: str, system: str, title: str) -> str:
    """
    Wraps retrieved document content in a secure structural XML boundary tag.
    Attributes and inner content are safely escaped to prevent delimiter and attribute injection.
    """
    safe_id = escape_xml_attribute(doc_id)
    safe_sys = escape_xml_attribute(system)
    safe_title = escape_xml_attribute(title)
    safe_content = sanitize_retrieved_content(content)
    return f'<retrieved_document id="{safe_id}" system="{safe_sys}" title="{safe_title}">\n{safe_content}\n</retrieved_document>'


try:
    from rag_models import KnowledgeArticle, SearchResult, DocumentSummary, SectionHierarchy
except ImportError:
    from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import KnowledgeArticle, SearchResult, DocumentSummary, SectionHierarchy

try:
    from it_helpdesk_agent.app_utils.system_config import get_valid_system_filters, get_retrieval_config
    from it_helpdesk_agent.app_utils.embedding_utils import DEFAULT_EMBEDDING_MODEL, generate_text_embedding
    from it_helpdesk_agent.app_utils.reranker import rerank_search_results
except ImportError:
    try:
        from app_utils.system_config import get_valid_system_filters, get_retrieval_config
        from app_utils.embedding_utils import DEFAULT_EMBEDDING_MODEL, generate_text_embedding
        from app_utils.reranker import rerank_search_results
    except ImportError:
        def get_valid_system_filters() -> set[str]:
            return {"ERP", "HRM", "CRM", "ALL"}
        def get_retrieval_config() -> dict[str, Any]:
            return {"fraction_lists_to_search": 0.05, "hybrid_search_enabled": False, "reranker_enabled": False}
        DEFAULT_EMBEDDING_MODEL = "text-multilingual-embedding-002"
        def generate_text_embedding(text: str, **kwargs) -> list[float]:
            return [0.0] * 768
        def rerank_search_results(query: str, candidates: list[SearchResult], **kwargs) -> list[SearchResult]:
            return candidates


class KnowledgeStoreUnavailableError(Exception):
    """Raised when the primary enterprise knowledge store backend (e.g. BigQuery) fails or is unreachable."""
    pass


import datetime

# Built-in Enterprise Knowledge Base for Local Development & Testing
ENTERPRISE_ARTICLES: list[KnowledgeArticle] = [
    # --- ERP ARTICLES (10) ---
    KnowledgeArticle(
        id="ERP-KB-001",
        system="ERP",
        title="Khắc phục lỗi phân quyền Purchase Order (SAP/Oracle M_BEST_EKO)",
        category="Finance & Procurement",
        content="""Khi người dùng gặp lỗi 'Authorization check failed for Object M_BEST_EKO (Activity 01/02)':
1. Nguyên nhân: Tài khoản chưa được gán Role Z_PROC_PURCHASER hoặc Purchasing Group bị giới hạn trong bảng T024.
2. Quy trình xử lý:
   - Yêu cầu người dùng cung cấp mã Purchase Organization và Purchasing Group.
   - Gửi yêu cầu phê duyệt đến Trưởng bộ phận mua hàng (Procurement Manager).
   - Sau khi có phê duyệt, IT Admin gán T-code ME21N/ME22N và object M_BEST_EKO thông qua hệ thống phân quyền SAP GRC.
3. SLA xử lý: 2 giờ làm việc kể từ khi có đủ phê duyệt.""",
        keywords=["erp", "sap", "oracle", "purchase order", "m_best_eko", "me21n", "procurement", "phân quyền", "po"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Phân quyền & Mua hàng", h3="Lỗi M_BEST_EKO"),
        source_uri="docs/erp_po_manual.md",
        owner="erp-team@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="ERP-KB-002",
        system="ERP",
        title="Hướng dẫn xử lý lỗi khóa kỳ kế toán (Posting Period Locked)",
        category="Accounting",
        content="""Lỗi 'Posting period 08/2026 is closed':
1. Kiểm tra trạng thái kỳ kế toán bằng T-code OB52 (FI) hoặc MMPV (MM).
2. Quy trình:
   - Kế toán trưởng phải gửi email xác nhận mở kỳ phụ (Special Period 13-16).
   - IT ERP Team chỉ được mở tạm thời trong khung giờ 17:00 - 19:00 sau khi có ticket phê duyệt.
   - Ghi log audit thay đổi trạng thái OB52.""",
        keywords=["erp", "kỳ kế toán", "posting period", "ob52", "mmpv", "khóa sổ", "sap", "oracle"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Kế toán tài chính", h3="Khóa kỳ OB52"),
        source_uri="docs/erp_period_lock.md",
        owner="erp-finance@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="ERP-KB-003",
        system="ERP",
        title="In phiếu xuất kho và kiểm kê tồn kho SAP Material Management (T-code MB52, MIGO)",
        category="Inventory & Warehouse",
        content="""Hướng dẫn kiểm kê tồn kho và in phiếu xuất kho trong module SAP MM:
1. Sử dụng T-code MB52 để xem báo cáo tồn kho theo Plant và Storage Location.
2. Thực hiện xuất kho hàng hóa theo đơn hàng bằng T-code MIGO (Movement Type 201/261).
3. Trường hợp lệch số lượng giữa vật lý và hệ thống: Tạo biên bản kiểm kê và gửi thủ kho ký duyệt.""",
        keywords=["erp", "sap", "inventory", "xuất kho", "tồn kho", "mb52", "migo", "kho vận", "material management"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Quản lý kho vận", h3="Kiểm kê MB52"),
        source_uri="docs/erp_inventory_migo.md",
        owner="erp-warehouse@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="ERP-KB-004",
        system="ERP",
        title="Cập nhật tỷ giá hối đoái ngoại tệ tự động trong module kế toán SAP FI (T-code OB08)",
        category="Accounting",
        content="""Quy trình cập nhật tỷ giá ngoại tệ USD/EUR/JPY hàng ngày trong hệ thống SAP:
1. Tỷ giá được job tự động fetch từ ngân hàng trung ương lúc 08:30 sáng vào bảng TCURR.
2. Kiểm tra tỷ giá hiện hành qua T-code OB08.
3. Nếu job tự động lỗi do API timeout: Kế toán viên nhập tỷ giá thủ công và yêu cầu phê duyệt từ Kế toán trưởng.""",
        keywords=["erp", "sap", "tỷ giá", "ngoại tệ", "currency", "ob08", "kế toán", "fi", "tcurr"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Kế toán tài chính", h3="Tỷ giá OB08"),
        source_uri="docs/erp_exchange_rates.md",
        owner="erp-finance@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="ERP-KB-005",
        system="ERP",
        title="Quản lý danh mục nhà cung cấp Vendor Master Data (T-code XK01, BP)",
        category="Procurement",
        content="""Quy trình tạo mới và chỉnh sửa thông tin Nhà cung cấp (Vendor Master Data):
1. Trên SAP S/4HANA sử dụng transaction code BP (Business Partner) vai trò FLVN01.
2. Hồ sơ nhà cung cấp bắt buộc đính kèm: Giấy phép ĐKKD, Thông tin tài khoản ngân hàng và Mã số thuế.
3. Bộ phận Mua hàng gửi ticket phê duyệt Compliance trước khi kích hoạt Vendor.""",
        keywords=["erp", "sap", "vendor", "nhà cung cấp", "bp", "xk01", "mua hàng", "procurement", "master data"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Mua sắm", h3="Vendor BP"),
        source_uri="docs/erp_vendor_master.md",
        owner="erp-procure@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="ERP-KB-006",
        system="ERP",
        title="Lập hóa đơn bán hàng Billing Document và hạch toán doanh thu (T-code VF01, VF04)",
        category="Sales & Distribution",
        content="""Hướng dẫn xuất hóa đơn bán hàng (Billing Document) trên SAP SD:
1. Sử dụng T-code VF01 để tạo hóa đơn đơn lẻ từ Delivery Note, hoặc VF04 để chạy theo danh sách hàng loạt.
2. Khi phát sinh lỗi Account Determination: Kiểm tra bảng VKOA với Kế toán doanh thu.
3. Hóa đơn điện tử e-Invoice được tự động phát hành và ký số token qua cổng CQT.""",
        keywords=["erp", "sap", "billing", "hóa đơn", "doanh thu", "vf01", "vf04", "sales", "sd", "vkoa"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Bán hàng & Phân phối", h3="Hóa đơn VF01"),
        source_uri="docs/erp_billing_vf01.md",
        owner="erp-sd@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="ERP-KB-007",
        system="ERP",
        title="Xử lý lỗi hạch toán chi phí lương tự động từ HRM sang ERP FI/CO",
        category="Accounting",
        content="""Khắc phục lỗi batch job hạch toán lương cuối tháng từ module HRM sang SAP General Ledger:
1. Nguyên nhân: Cost Center (Trung tâm chi phí) bị khóa hoặc Cost Element chưa được định nghĩa trong CO.
2. Kiểm tra Cost Center bằng T-code KS03 và GL Account qua FS00.
3. Kế toán chi phí mở khóa Cost Center và kích hoạt lại job hạch toán lương qua T-code PCP0.""",
        keywords=["erp", "sap", "hạch toán", "lương", "chi phí", "fico", "cost center", "ks03", "pcp0"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Kế toán quản trị", h3="Hạch toán chi phí lương"),
        source_uri="docs/erp_payroll_posting.md",
        owner="erp-finance@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="ERP-KB-008",
        system="ERP",
        title="Quy trình phê duyệt đề xuất mua sắm Purchase Requisition (PR to PO, T-code ME51N)",
        category="Procurement",
        content="""Quy trình tạo và phê duyệt Yêu cầu mua sắm (Purchase Requisition - PR):
1. Người dùng phòng ban tạo PR bằng T-code ME51N, chọn Document Type NB và gán Cost Center.
2. PR được tự động chuyển lên cấp Quản lý trực tiếp và Giám đốc Khối phê duyệt qua Fiori Inbox.
3. Sau khi PR được Release hoàn tất, phòng Mua hàng sử dụng ME21N để tạo Purchase Order tham chiếu PR.""",
        keywords=["erp", "sap", "purchase requisition", "pr", "me51n", "đề xuất mua sắm", "duyệt pr", "me21n"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Mua sắm", h3="Purchase Requisition ME51N"),
        source_uri="docs/erp_purchase_requisition.md",
        owner="erp-procure@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="ERP-KB-009",
        system="ERP",
        title="Báo cáo phân tích ngân sách dự án và chi phí đầu tư (SAP PS T-code CJ20N)",
        category="Project Systems",
        content="""Quản lý và kiểm soát ngân sách đầu tư dự án (Project System - PS):
1. Sử dụng T-code CJ20N (Project Builder) để cấu hình WBS Element và phân bổ hạn mức chi phí.
2. Kiểm tra tình hình giải ngân ngân sách thực tế so với kế hoạch bằng báo cáo S_ALR_87013558.
3. Khi vượt ngân sách dự toán: Yêu cầu tạo Ticket xin điều chỉnh Budget Supplement.""",
        keywords=["erp", "sap", "ngân sách", "budget", "cj20n", "dự án", "project system", "wbs", "giải ngân"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Quản lý dự án", h3="Ngân sách CJ20N"),
        source_uri="docs/erp_project_budget.md",
        owner="erp-pm@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="ERP-KB-010",
        system="ERP",
        title="Hướng dẫn kết chuyển số dư tài khoản cuối năm tài chính (T-code F.07, F.16)",
        category="Accounting",
        content="""Quy trình đóng sổ và kết chuyển số dư tài chính cuối năm (Year-End Closing):
1. Chạy T-code F.07 để kết chuyển số dư công nợ Vendor/Customer (AP/AR Balance Carryforward).
2. Chạy T-code F.16 (GL Balance Carryforward) để kết chuyển số dư tài khoản Sổ cái sang năm tài chính mới.
3. Đối chiếu Bảng cân đối thử (Trial Balance) và lập biên bản khóa sổ cuối năm.""",
        keywords=["erp", "sap", "kết chuyển", "số dư", "f.07", "f.16", "năm tài chính", "khóa sổ năm", "balance carryforward"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu ERP", h2="Kế toán tài chính", h3="Khóa sổ năm F.07"),
        source_uri="docs/erp_year_end_closing.md",
        owner="erp-finance@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),

    # --- HRM ARTICLES (10) ---
    KnowledgeArticle(
        id="HRM-KB-101",
        system="HRM",
        title="Xử lý lỗi đồng bộ chấm công và khóa bảng công Workday/BambooHR",
        category="Timesheet & Payroll",
        content="""Vấn đề nhân viên không thấy dữ liệu chấm công từ máy vân tay hoặc FaceID:
1. Nguyên nhân thường gặp:
   - Service 'HR-Biometric-Sync' tại server 10.0.12.55 bị dừng.
   - Mã nhân viên (Employee ID) trên máy chấm công không khớp với mã trong HRM Core.
2. Các bước xử lý:
   - Bước 1: Kiểm tra kết nối mạng máy chấm công tại chi nhánh qua ping IP nội bộ.
   - Bước 2: Restart cronjob sync: `systemctl restart hr-sync-agent`.
   - Bước 3: Nếu bảng công tháng đã bị 'Payroll Locked' sau ngày 25 hàng tháng, yêu cầu HR Operations gửi ticket mở khóa ngoại lệ.""",
        keywords=["hrm", "workday", "bamboohr", "chấm công", "timesheet", "vân tay", "payroll", "bảng lương"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Chấm công & Bảng lương", h3="Đồng bộ Biometric"),
        source_uri="docs/hrm_timesheet_sync.md",
        owner="hrm-ops@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="HRM-KB-102",
        system="HRM",
        title="Quy trình Onboarding & Cấp phát tài khoản nhân sự mới tự động",
        category="Identity & Access Management",
        content="""Quy trình cấp tài khoản tự động từ HRM sang Active Directory & Google Workspace:
1. Dữ liệu nhân sự mới từ HR tuyển dụng được nhập vào HRM trước ngày làm việc 3 ngày.
2. Job đồng bộ tự động chạy lúc 00:00 hàng ngày:
   - Tạo email theo cú pháp `firstname.lastname@company.com`.
   - Gán group bảo mật theo phòng ban và chức danh (ví dụ: `all-sales@company.com`).
   - Cấp tài khoản SSO Okta / Microsoft Entra ID.
3. Nếu nhân viên mới không nhận được thông tin đăng nhập: Kiểm tra trạng thái 'Pending Approval' trong module Onboarding của HRM.""",
        keywords=["hrm", "onboarding", "nhân viên mới", "cấp tài khoản", "active directory", "email", "okta"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Onboarding & Tuyển dụng", h3="Cấp tài khoản tự động"),
        source_uri="docs/hrm_onboarding.md",
        owner="hrm-iam@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="HRM-KB-103",
        system="HRM",
        title="Quy trình đăng ký nghỉ phép năm và phê duyệt ngày nghỉ trên BambooHR/Workday",
        category="Leave & Attendance",
        content="""Hướng dẫn nộp đơn và phê duyệt nghỉ phép năm (Annual Leave):
1. Nhân viên truy cập portal BambooHR/Workday -> mục Time Off -> Tạo yêu cầu nghỉ phép mới.
2. Đơn nghỉ từ 1-2 ngày được Quản lý trực tiếp phê duyệt; đơn trên 3 ngày cần Giám đốc Khối xác nhận.
3. Số ngày phép dư cuối năm được tự động chuyển tối đa 5 ngày sang quý 1 năm kế tiếp.""",
        keywords=["hrm", "nghỉ phép", "leave request", "annual leave", "phép năm", "workday", "bamboohr", "time off"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Chế độ nghỉ phép", h3="Đơn nghỉ phép năm"),
        source_uri="docs/hrm_annual_leave.md",
        owner="hrm-policy@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="HRM-KB-104",
        system="HRM",
        title="Hướng dẫn khai báo người phụ thuộc và đăng ký giảm trừ gia cảnh thuế TNCN",
        category="Payroll & Taxation",
        content="""Quy trình đăng ký hồ sơ giảm trừ gia cảnh thuế thu nhập cá nhân (TNCN):
1. Chuẩn bị giấy tờ: Giấy khai sinh con, CCCD bố mẹ phụ thuộc, bản cam kết nuôi dưỡng.
2. Tải biểu mẫu số 02/ĐK-NPT-TNCN và upload lên mục Thuế & Phúc lợi trên cổng HRM.
3. Chuyên viên C&B kiểm tra và nộp hồ sơ điện tử cho cơ quan Thuế vào ngày 15 hàng tháng.""",
        keywords=["hrm", "thuế tncn", "người phụ thuộc", "giảm trừ gia cảnh", "pit", "nhân sự", "c&b", "bảng lương"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Tiền lương & Thuế", h3="Giảm trừ gia cảnh TNCN"),
        source_uri="docs/hrm_tax_dependents.md",
        owner="hrm-cb@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="HRM-KB-105",
        system="HRM",
        title="Khắc phục sự cố truy cập phiếu lương điện tử Payslip trên ứng dụng di động HRM",
        category="Self-Service Portal",
        content="""Xử lý lỗi nhân viên không thể mở hoặc tải file Phiếu lương Payslip (PDF):
1. Đảm bảo nhân viên đã xác thực sinh trắc học FaceID/TouchID hoặc nhập mã PIN 6 số trên ứng dụng.
2. Phiếu lương được mã hóa mật khẩu mặc định là 6 số cuối CCCD + 2 số cuối năm sinh.
3. Nếu ứng dụng báo lỗi 'Payslip generation failed': Liên hệ bộ phận C&B để trigger lại render PDF.""",
        keywords=["hrm", "payslip", "phiếu lương", "mobile app", "xem lương", "bảo mật lương", "mật khẩu pdf"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Cổng tự phục vụ", h3="Phiếu lương Payslip"),
        source_uri="docs/hrm_payslip_troubleshoot.md",
        owner="hrm-it@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="HRM-KB-106",
        system="HRM",
        title="Quy trình bàn giao tài sản và offboarding tài khoản khi nhân viên nghỉ việc",
        category="Offboarding & Separation",
        content="""Quy trình chấm dứt hợp đồng lao động và thu hồi quyền truy cập (Offboarding):
1. Nhân sự tạo ticket 'Employee Termination' trước ngày làm việc cuối cùng 7 ngày.
2. Vào ngày làm việc cuối: Bàn giao máy tính xách tay, thẻ ra vào tòa nhà cho IT và Hành chính.
3. Vào đúng 18:00 ngày nghỉ việc: Toàn bộ tài khoản SSO, Email, VPN bị khóa tự động theo kịch bản IAM.""",
        keywords=["hrm", "offboarding", "nghỉ việc", "bàn giao tài sản", "thu hồi tài khoản", "active directory", "thu hồi quyền"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Nghỉ việc & Bàn giao", h3="Quy trình Offboarding"),
        source_uri="docs/hrm_offboarding_process.md",
        owner="hrm-ops@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="HRM-KB-107",
        system="HRM",
        title="Đánh giá hiệu suất công việc định kỳ KPI & OKR trên hệ thống quản trị nhân tài",
        category="Performance Management",
        content="""Hướng dẫn chu kỳ đánh giá hiệu suất (Performance Review Cycle):
1. Nhân viên hoàn thành bản Tự đánh giá (Self-Assessment) trên HRM trước ngày 20 cuối quý.
2. Quản lý trực tiếp tổ chức phiên họp 1-on-1 Feedback và chấm điểm hiệu suất theo khung thang điểm 1-5.
3. Kết quả đánh giá được chuyển giao cho Ủy ban Đãi ngộ để xét thưởng thành tích và điều chỉnh lương.""",
        keywords=["hrm", "kpi", "okr", "đánh giá hiệu suất", "performance review", "talent management", "thưởng thành tích"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Quản trị hiệu suất", h3="Đánh giá KPI OKR"),
        source_uri="docs/hrm_performance_review.md",
        owner="hrm-talent@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="HRM-KB-108",
        system="HRM",
        title="Cấu hình ca làm việc luân phiên Shift Scheduling và phụ cấp làm đêm",
        category="Time & Attendance",
        content="""Quản lý lịch phân ca và tính toán phụ cấp làm thêm giờ/làm ca đêm:
1. Trưởng ca xếp lịch trực trước thứ 6 hàng tuần qua module Shift Planner.
2. Ca đêm (22:00 - 06:00) được hệ thống tự động cộng thêm 30% phụ cấp lương theo Bộ luật Lao động.
3. Đổi ca khẩn cấp: Cần có xác nhận chéo giữa 2 nhân viên và được Trưởng ca phê duyệt trên app.""",
        keywords=["hrm", "ca kíp", "shift scheduling", "làm đêm", "phụ cấp ca", "lịch làm việc", "đổi ca"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Chấm công & Ca kíp", h3="Phân ca làm việc"),
        source_uri="docs/hrm_shift_scheduling.md",
        owner="hrm-ops@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="HRM-KB-109",
        system="HRM",
        title="Đăng ký thủ tục Bảo hiểm Xã hội (BHXH) và cấp thẻ BHYT cho nhân sự mới",
        category="Social Insurance & Benefits",
        content="""Quy trình báo tăng lao động và cấp sổ BHXH/thẻ BHYT điện tử:
1. Tiếp nhận mã số BHXH hoặc đăng ký cấp mới mã số qua phần mềm kê khai bảo hiểm điện tử.
2. Nộp hồ sơ mẫu D02-LT cho cơ quan BHXH quận trước ngày 20 của tháng bắt đầu ký HĐLĐ.
3. Hướng dẫn nhân viên cài đặt ứng dụng VssID để theo dõi quá trình đóng bảo hiểm.""",
        keywords=["hrm", "bhxh", "bảo hiểm xã hội", "bhyt", "chế độ thai sản", "ốm đau", "vssid", "báo tăng lao động"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Bảo hiểm & Phúc lợi", h3="Thủ tục BHXH"),
        source_uri="docs/hrm_social_insurance.md",
        owner="hrm-cb@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="HRM-KB-110",
        system="HRM",
        title="Khai báo và hoàn ứng chi phí công tác (Business Travel & Expense) qua cổng HR Travel",
        category="Travel & Expense",
        content="""Quy trình duyệt công tác và thanh quyết toán chi phí công tác (Expense Claim):
1. Tạo Kế hoạch công tác (Travel Request) kèm dự toán chi phí vé máy bay, khách sạn, công tác phí.
2. Sau khi hoàn thành chuyến đi: Upload hóa đơn VAT điện tử trong vòng 5 ngày làm việc.
3. Kế toán thanh toán giải ngân hoàn ứng vào tài khoản ngân hàng của nhân viên trong kỳ lương tiếp theo.""",
        keywords=["hrm", "công tác phí", "travel expense", "hoàn ứng", "vé máy bay", "khách sạn", "hóa đơn công tác"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu HRM", h2="Công tác & Chi phí", h3="Hoàn ứng công tác phí"),
        source_uri="docs/hrm_travel_expense.md",
        owner="hrm-finance@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),

    # --- CRM ARTICLES (10) ---
    KnowledgeArticle(
        id="CRM-KB-201",
        system="CRM",
        title="Sự cố đồng bộ Lead & Cơ hội giữa CRM và Marketing Automation (Salesforce/HubSpot)",
        category="Sales Operations",
        content="""Khi đội Sales báo cáo Lead từ Web Form không đồng bộ vào CRM:
1. Kiểm tra Webhook Endpoint và API Limits của Salesforce/HubSpot:
   - Giới hạn 24h API Calls: Đảm bảo chưa vượt ngưỡng 90% Daily Limit.
   - Kiểm tra trạng thái OAuth Token của Integration User: Nếu token hết hạn, yêu cầu Admin re-authenticate.
2. Kiểm tra Validation Rules: Các trường bắt buộc như 'Country', 'Phone Number Standard' bị từ chối do dữ liệu thô không hợp lệ.
3. Khắc phục: Chạy lại batch error queue trong CRM Integration Manager.""",
        keywords=["crm", "salesforce", "hubspot", "lead", "đồng bộ", "oauth", "api limit", "webhook", "sales"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Sales & Marketing Sync", h3="Webhook & API Limit"),
        source_uri="docs/crm_lead_sync.md",
        owner="crm-admin@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="CRM-KB-202",
        system="CRM",
        title="Phân quyền Territory & Chuyển giao Account khách hàng trên CRM",
        category="Customer Relationship",
        content="""Hướng dẫn chuyển giao quản lý Account khi có thay đổi nhân sự Sales:
1. Điều kiện: Quản lý bộ phận (Sales Manager) tạo ticket chỉ định Sales Rep nhận chuyển giao.
2. Các bước:
   - Vào CRM -> Mass Transfer Records.
   - Chọn chuyển giao: Accounts, Open Opportunities, Open Cases, và Activity History.
   - Bỏ tích 'Transfer Closed Opportunities' nếu quy chế hoa hồng năm cũ vẫn giữ nguyên.
3. Thông báo cho Sales Rep mới qua email tự động sau khi transfer hoàn tất.""",
        keywords=["crm", "territory", "transfer account", "sales rep", "khách hàng", "salesforce", "hubspot"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Territory Management", h3="Chuyển giao Account"),
        source_uri="docs/crm_territory_transfer.md",
        owner="crm-ops@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="CRM-KB-203",
        system="CRM",
        title="Hướng dẫn tạo bảng báo giá Quote Template và quy trình phê duyệt chiết khấu Salesforce",
        category="Sales Operations",
        content="""Quy trình tạo Báo giá (Quote) và duyệt chiết khấu đặc biệt (Discount Approval):
1. Từ Opportunity tương ứng, nhấn 'New Quote', chọn bảng giá (Price Book) và thêm sản phẩm.
2. Mức chiết khấu từ 0-10% tự động duyệt; từ 11-20% cần Sales Director duyệt; trên 20% cần CFO duyệt qua email.
3. Sau khi Quote được duyệt: Nhấn 'Generate PDF' để gửi báo giá chính thức có watermark bảo mật cho khách hàng.""",
        keywords=["crm", "salesforce", "quote", "báo giá", "chiết khấu", "discount approval", "cpq", "price book"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Báo giá & Hợp đồng", h3="Tạo Quote & Duyệt Chiết khấu"),
        source_uri="docs/crm_quote_template.md",
        owner="crm-salesops@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="CRM-KB-204",
        system="CRM",
        title="Khắc phục lỗi trùng lặp dữ liệu khách hàng Contact & Account (Duplicate Rules)",
        category="Data Quality",
        content="""Xử lý trùng lặp dữ liệu khách hàng tiềm năng trên Salesforce/HubSpot:
1. Hệ thống tự động kích hoạt Matching Rule dựa trên trùng Email hoặc Số điện thoại chuẩn hóa E.164.
2. Sử dụng công cụ 'Merge Contacts' để gộp tối đa 3 bản ghi trùng, giữ lại thông tin Master Record mới nhất.
3. Toàn bộ lịch sử hoạt động, ghi chú cuộc gọi và Opportunity liên quan được kế thừa đầy đủ sau khi gộp.""",
        keywords=["crm", "salesforce", "duplicate", "trùng lặp", "matching rules", "gộp khách hàng", "merge", "data cleaning"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Chất lượng dữ liệu", h3="Xử lý trùng lặp"),
        source_uri="docs/crm_duplicate_rules.md",
        owner="crm-admin@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="CRM-KB-205",
        system="CRM",
        title="Thiết lập chiến dịch tiếp thị Campaign Marketing và theo dõi ROI trên HubSpot CRM",
        category="Marketing Operations",
        content="""Quy trình quản lý Campaign Marketing và đo lường tỷ lệ chuyển đổi ROI:
1. Tạo Campaign mới, gắn ngân sách dự kiến và UTM parameters theo chuẩn `utm_campaign=q3_event`.
2. Gắn form landing page và luồng email nuôi dưỡng Lead (Nurturing Workflow) vào Campaign.
3. Xem báo cáo 'Campaign Influence' để xác định các giao dịch Closed-Won được đóng góp từ chiến dịch.""",
        keywords=["crm", "hubspot", "campaign", "chiến dịch", "marketing", "roi", "lead scoring", "utm tracking"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Tiếp thị & Chiến dịch", h3="Thiết lập Campaign"),
        source_uri="docs/crm_campaign_roi.md",
        owner="crm-marketing@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="CRM-KB-206",
        system="CRM",
        title="Phân quyền truy cập Báo cáo & Dashboard phân tích phễu bán hàng (Sales Pipeline)",
        category="Analytics & Reporting",
        content="""Cấu hình phân quyền thư mục Báo cáo (Report Folders) và Dashboard:
1. Thư mục 'Executive KPI Dashboard' chỉ dành cho Ban Tổng giám đốc và Giám đốc Khối (View Only).
2. Trưởng nhóm bán hàng được cấp quyền 'Manage' trên thư mục báo cáo của Team mình.
3. Báo cáo tự động được đặt lịch gửi email tổng kết (Scheduled Refresh) vào 08:00 sáng thứ Hai hàng tuần.""",
        keywords=["crm", "salesforce", "dashboard", "báo cáo", "sales pipeline", "phễu bán hàng", "analytics", "báo cáo tuần"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Phân tích & Báo cáo", h3="Phân quyền Dashboard"),
        source_uri="docs/crm_dashboard_reports.md",
        owner="crm-bi@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="CRM-KB-207",
        system="CRM",
        title="Tích hợp tổng đài ảo VoIP Cloud CTI để tự động ghi âm cuộc gọi trên CRM",
        category="Computer Telephony Integration",
        content="""Hướng dẫn kết nối máy nhánh tổng đài VoIP (Cloud PBX / CTI) với hồ sơ khách hàng:
1. Cài đặt extension 'CTI Softphone' trên trình duyệt Chrome và đăng nhập bằng tài khoản tổng đài SIP.
2. Cuộc gọi đến tự động hiển thị Pop-up thông tin khách hàng tương ứng (Screen Pop).
3. File ghi âm và thời lượng cuộc gọi tự động lưu vào mục Activity Timeline của Contact/Lead sau khi gác máy.""",
        keywords=["crm", "voip", "cti", "tổng đài", "ghi âm cuộc gọi", "telephony", "sales call", "softphone"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Tổng đài & Giao tiếp", h3="Tích hợp VoIP CTI"),
        source_uri="docs/crm_voip_cti.md",
        owner="crm-telecom@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="CRM-KB-208",
        system="CRM",
        title="Ký hợp đồng điện tử và tích hợp DocuSign / Adobe Sign trên Salesforce CRM",
        category="Contract Management",
        content="""Quy trình gửi hợp đồng kinh tế và theo dõi tiến độ chữ ký số điện tử:
1. Từ màn hình Contract hoặc Opportunity, nhấn nút 'Send with DocuSign'.
2. Chọn template hợp đồng mẫu, kéo thả các trường chữ ký, họ tên và ngày ký của Đại diện pháp luật khách hàng.
3. Sau khi tất cả các bên hoàn tất ký số: File hợp đồng hoàn chỉnh có chứng thực dấu thời gian được tự động đính kèm vào CRM.""",
        keywords=["crm", "salesforce", "docusign", "hợp đồng điện tử", "chữ ký số", "e-signature", "contract"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Hợp đồng & Pháp chế", h3="Chữ ký số DocuSign"),
        source_uri="docs/crm_docusign_contract.md",
        owner="crm-legal@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="CRM-KB-209",
        system="CRM",
        title="Khắc phục sự cố gửi email tự động (Email Deliverability DKIM SPF) trong Marketing Cloud",
        category="Marketing Operations",
        content="""Xử lý sự cố email marketing bị rơi vào hòm thư rác (Spam) hoặc bị trả về (Bounced):
1. Kiểm tra bản ghi DNS xác thực Sender Authentication Package (SAP): SPF, DKIM 2048-bit, và DMARC.
2. Kiểm tra danh sách Bounce List: Loại bỏ ngay các địa chỉ Hard Bounce để bảo vệ chỉ số IP Reputation.
3. Đảm bảo tất cả email gửi hàng loạt đều có liên kết hủy đăng ký (One-Click Unsubscribe) theo chuẩn quốc tế.""",
        keywords=["crm", "marketing cloud", "email", "dkim", "spf", "bounce email", "deliverability", "spam", "ip reputation"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Tiếp thị & Email", h3="Email Deliverability DKIM"),
        source_uri="docs/crm_email_deliverability.md",
        owner="crm-marketing@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
    KnowledgeArticle(
        id="CRM-KB-210",
        system="CRM",
        title="Quy trình định tuyến Case hỗ trợ kỹ thuật khách hàng đa kênh Omnichannel Routing",
        category="Customer Support",
        content="""Cấu hình phân bổ Ticket / Case hỗ trợ khách hàng tự động (Omnichannel Routing):
1. Luồng tiếp nhận từ Email, LiveChat và Zalo OA được tự động gom về hàng đợi (Queue) phân theo kỹ năng.
2. Hệ thống phân bổ Case cho nhân viên Support có trạng thái 'Available' và tải công việc (Workload Capacity) thấp nhất.
3. Cảnh báo tự động gửi cho Trưởng ca nếu Case mức độ nghiêm trọng High/Critical chưa được phản hồi sau 15 phút.""",
        keywords=["crm", "case", "omnichannel", "hỗ trợ khách hàng", "service cloud", "routing ticket", "queue", "hỗ trợ đa kênh"],
        section_hierarchy=SectionHierarchy(h1="Tài liệu CRM", h2="Dịch vụ khách hàng", h3="Định tuyến Omnichannel"),
        source_uri="docs/crm_omnichannel_routing.md",
        owner="crm-support@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
    ),
]


class BaseKnowledgeStore(ABC):
    """Abstract Base Class for Enterprise Knowledge Stores (Adapter Pattern)."""

    @abstractmethod
    def search(
        self,
        query: str,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None,
        user_roles: Optional[list[str]] = None,
        user_clearance: int = 0,
    ) -> list[SearchResult]:
        """Search knowledge articles matching the query, system filter, and authorized domain list."""
        pass

    @abstractmethod
    def get_article_by_id(self, article_id: str) -> Optional[KnowledgeArticle]:
        """Retrieve the full content of an article by its unique ID."""
        pass


class InMemoryKnowledgeStore(BaseKnowledgeStore):
    """
    In-memory knowledge store supporting fast keyword-based and hybrid retrieval.
    Ideal for local development, rapid prototyping, and unit testing.
    """

    def __init__(self, articles: list[KnowledgeArticle] = ENTERPRISE_ARTICLES):
        self.articles = articles

    def search(
        self,
        query: str,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None,
        user_roles: Optional[list[str]] = None,
        user_clearance: int = 0,
    ) -> list[SearchResult]:
        """Search knowledge articles by query keywords, system filter, and authorized systems."""
        valid_systems = get_valid_system_filters()
        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in valid_systems:
            clean_system = "ALL"

        allowed_upper = set(s.upper() for s in allowed_systems) if allowed_systems is not None else None

        # Resolve effective user roles for document-level security trimming
        effective_roles = user_roles
        if effective_roles is None:
            try:
                from it_helpdesk_agent.app_utils.sso_auth import get_current_sso_user
                user = get_current_sso_user()
                if user:
                    effective_roles = user.roles
            except ImportError:
                pass

        clean_user_roles = [r.lower().strip() for r in effective_roles] if effective_roles is not None else None
        is_admin_user = any(r in ("admin", "it_admin", "security_admin") for r in clean_user_roles) if clean_user_roles is not None else False

        effective_clearance = user_clearance
        if clean_user_roles is not None:
            if is_admin_user:
                effective_clearance = 3
            elif any(r in ("hr_admin", "finance_admin", "compliance_officer", "legal_counsel", "hr_manager", "finance_manager") for r in clean_user_roles):
                effective_clearance = 2
            elif clean_user_roles:
                effective_clearance = 1
            else:
                effective_clearance = 0
        else:
            effective_clearance = user_clearance if user_clearance > 0 else 3

        # Content Governance: Filter out expired and future-effective documents
        today_str = datetime.date.today().isoformat()

        # Check if hybrid search is enabled in configuration
        retrieval_cfg = get_retrieval_config()
        hybrid_enabled = retrieval_cfg.get("hybrid_search_enabled", True)

        # Common Vietnamese and English stop words
        STOP_WORDS = {
            "và", "các", "cho", "của", "là", "ở", "trong", "trên", "được", "với", "tại",
            "để", "khi", "có", "này", "đó", "ra", "vào", "lại", "nào", "gì", "sao",
            "làm", "như", "thế", "theo", "từ", "bị", "đã", "sẽ", "phải", "về", "hãy",
            "giúp", "tôi", "bạn", "cách", "hướng", "dẫn", "quy", "định", "bao", "nhiêu",
            "mục", "nằm", "sau", "đến", "hoặc", "một", "hai", "ba", "bốn", "năm",
            "the", "a", "an", "in", "on", "at", "to", "for", "of", "and", "or", "is", "are"
        }

        query_lower = query.lower()
        raw_terms = re.findall(r'[\w\-]+', query_lower)
        terms = [t for t in raw_terms if t not in STOP_WORDS and len(t) > 1]
        if not terms:
            terms = raw_terms

        results: list[tuple[float, KnowledgeArticle]] = []

        for article in self.articles:
            # 1. Tombstone filter: Exclude deleted articles (Fail-Closed)
            if getattr(article, "is_deleted", False):
                continue

            # 2. Content Governance date filtering
            if article.expiry_date and article.expiry_date < today_str:
                continue
            if article.effective_date and article.effective_date > today_str:
                continue

            # 3. RBAC & System filter
            art_sys = article.system.upper()
            if clean_system != "ALL" and art_sys != clean_system:
                continue
            if allowed_upper is not None and art_sys not in allowed_upper:
                continue

            # 3b. Document-Level Clearance Level Pre-Filter
            art_clearance = getattr(article, "clearance_level", None)
            if art_clearance is None:
                doc_sens = (getattr(article, "sensitivity", "INTERNAL") or "INTERNAL").upper()
                art_clearance = 0 if doc_sens == "PUBLIC" else (2 if doc_sens == "CONFIDENTIAL" else (3 if doc_sens == "RESTRICTED" else 1))
            if art_clearance > effective_clearance:
                continue

            # 3c. Document-Level RBAC: allowed_roles
            doc_allowed = [r.lower().strip() for r in (getattr(article, "allowed_roles", None) or [])]
            if doc_allowed and clean_user_roles is not None and not is_admin_user:
                if not any(r in clean_user_roles for r in doc_allowed):
                    continue

            # 3d. Document-Level Sensitivity trimming
            doc_sens = getattr(article, "sensitivity", "INTERNAL") or "INTERNAL"
            if doc_sens.upper() in ("CONFIDENTIAL", "RESTRICTED") and clean_user_roles is not None and not is_admin_user:
                if not any(r in clean_user_roles for r in doc_allowed) and not any(r in ("hr_admin", "finance_admin", "security_admin") for r in clean_user_roles):
                    continue

            score = 0.0
            article_text = f"{article.title} {article.category} {article.content}".lower()
            article_keywords = [k.lower() for k in article.keywords]

            # 4. Keyword matching & Exact match boosting (M_BEST_EKO, ME21N, OB52, etc.)
            for term in terms:
                # Exact phrase / code matching (case-insensitive)
                if term in article.title.lower():
                    score += 3.0
                elif term in article_keywords:
                    score += 2.0
                elif term in article_text:
                    score += 0.5

            # Exact technical code / transaction code matching bonus
            for kw in article_keywords:
                if len(kw) >= 3 and kw in query_lower:
                    score += 4.0

            # Additional hybrid scoring bonus when hybrid search is enabled
            if hybrid_enabled:
                for term in terms:
                    if len(term) >= 4 and term in article_text:
                        score += 1.5

            # Minimum relevance threshold: require at least a meaningful keyword match
            if score >= 2.0:
                results.append((score, article))

        # Sort by relevance score descending
        results.sort(key=lambda x: x[0], reverse=True)

        search_results = []
        for score, article in results[:limit]:
            is_truncated = len(article.content) > 1200
            raw_snippet = article.content[:1200].strip() + "..." if is_truncated else article.content.strip()
            snippet = wrap_retrieved_document(
                content=raw_snippet,
                doc_id=article.id,
                system=article.system,
                title=article.title,
            )
            relevance = min(1.0, score / 6.0)
            sec_hier = article.section_hierarchy
            context_path = sec_hier.format_path() if sec_hier else f"{article.system} > {article.category} > {article.title}"
            search_results.append(SearchResult(
                article_id=article.id,
                parent_doc_id=article.parent_doc_id,
                chunk_index=article.chunk_index,
                system=article.system,
                title=article.title,
                snippet=snippet,
                relevance_score=round(relevance, 2),
                section_h1=article.section_h1,
                section_h2=article.section_h2,
                section_h3=article.section_h3,
                section_hierarchy=sec_hier,
                context_path=context_path,
                allowed_roles=article.allowed_roles,
                sensitivity=article.sensitivity,
                clearance_level=getattr(article, "clearance_level", None),
                source_uri=article.source_uri,
                category=article.category,
                keywords=article.keywords,
                owner=article.owner,
                effective_date=article.effective_date,
                expiry_date=article.expiry_date,
                is_deleted=getattr(article, "is_deleted", False),
                is_truncated=is_truncated,
            ))
        return search_results

    def get_article_by_id(self, article_id: str) -> Optional[KnowledgeArticle]:
        """Retrieves an article by its unique ID, aggregating multi-chunk documents if present."""
        clean_id = article_id.upper().strip()
        
        target_parent = None
        for a in self.articles:
            if a.id.upper() == clean_id:
                if a.parent_doc_id:
                    target_parent = a.parent_doc_id.upper()
                break
            elif a.parent_doc_id and a.parent_doc_id.upper() == clean_id:
                target_parent = a.parent_doc_id.upper()
                break

        if target_parent:
            matching = [a for a in self.articles if a.parent_doc_id and a.parent_doc_id.upper() == target_parent]
        else:
            matching = [a for a in self.articles if a.id.upper() == clean_id]

        if not matching:
            return None

        if len(matching) == 1 and not matching[0].parent_doc_id:
            return matching[0]

        sorted_chunks = sorted(matching, key=lambda x: getattr(x, "chunk_index", 0) or 0)
        base = sorted_chunks[0]
        aggregated_content = "\n\n".join(c.content for c in sorted_chunks if c.content)
        return KnowledgeArticle(
            id=base.parent_doc_id or base.id,
            parent_doc_id=base.parent_doc_id,
            chunk_index=0,
            system=base.system,
            title=base.title.split(" (Phần ")[0] if " (Phần " in base.title else base.title,
            category=base.category,
            content=aggregated_content,
            keywords=base.keywords,
            section_h1=base.section_h1,
            section_h2=base.section_h2,
            section_h3=base.section_h3,
            section_hierarchy=base.section_hierarchy,
            allowed_roles=base.allowed_roles,
            sensitivity=base.sensitivity,
            source_uri=base.source_uri,
            owner=base.owner,
            effective_date=base.effective_date,
            expiry_date=base.expiry_date,
            is_deleted=base.is_deleted,
            deleted_at=base.deleted_at,
        )


class BigQueryVectorKnowledgeStore(BaseKnowledgeStore):
    """
    Production-grade Knowledge Store using BigQuery Vector Search and Vertex AI Embeddings.
    Fails closed when BigQuery is unreachable rather than serving mismatched mock data.
    """

    def __init__(
        self,
        project_id: Optional[str] = None,
        dataset_id: Optional[str] = None,
        table_name: str = "knowledge_articles",
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        bq_client: Optional[Any] = None,
        embedding_fn: Optional[Any] = None,
    ):
        self.project_id = project_id or os.getenv("GOOGLE_CLOUD_PROJECT", "it-helpdesk-prod")
        self.dataset_id = dataset_id or os.getenv("BIGQUERY_KB_DATASET", "it_helpdesk_kb")
        self.table_name = table_name
        self.embedding_model = embedding_model
        self.embedding_fn = embedding_fn
        self._index_active_cache: Optional[tuple[bool, float]] = None

        if bq_client is not None:
            self.bq_client = bq_client
        else:
            try:
                from google.cloud import bigquery
                self.bq_client = bigquery.Client(project=self.project_id)
            except ImportError as e:
                logger.error("google-cloud-bigquery library is required for BigQueryVectorKnowledgeStore (%s).", e)
                raise ImportError(
                    "Thư viện 'google-cloud-bigquery' chưa được cài đặt. "
                    "Hãy cài đặt google-cloud-bigquery để sử dụng backend BigQuery."
                ) from e
            except Exception as e:
                logger.error("Failed to initialize BigQuery Client for Vector Search (%s).", e)
                self.bq_client = None

    def _generate_embedding(self, text: str, task_type: str = "RETRIEVAL_QUERY") -> list[float]:
        """Generates embedding using the shared enterprise embedding model or injected function."""
        if self.embedding_fn is not None:
            return self.embedding_fn(text)
        return generate_text_embedding(text, model_name=self.embedding_model, task_type=task_type)

    def _is_vector_index_active(self) -> bool:
        """Checks if BigQuery Vector Index exists, status is ACTIVE, and coverage >= 95.0. Caches result for 60 seconds."""
        now = time.time()
        if self._index_active_cache is not None:
            active, cached_time = self._index_active_cache
            if now - cached_time < 60:
                return active

        if not self.bq_client:
            return False

        try:
            sql = f"""
            SELECT index_status, coverage_percentage 
            FROM `{self.project_id}.{self.dataset_id}.INFORMATION_SCHEMA.VECTOR_INDEXES`
            WHERE table_name = '{self.table_name}'
            LIMIT 1
            """
            rows = list(self.bq_client.query(sql).result(timeout=5.0))
            if rows:
                status = getattr(rows[0], "index_status", "UNKNOWN")
                cov = getattr(rows[0], "coverage_percentage", None)
                is_active = (status == "ACTIVE" and cov is not None and float(cov) >= 95.0)
            else:
                is_active = False
            self._index_active_cache = (is_active, now)
            return is_active
        except Exception as e:
            logger.warning("Could not verify vector index coverage (%s), failing closed (assume inactive).", e)
            return False

    def search(
        self,
        query: str,
        system: str = "ALL",
        limit: int = 3,
        allowed_systems: Optional[list[str]] = None,
        user_roles: Optional[list[str]] = None,
        user_clearance: int = 0,
    ) -> list[SearchResult]:
        """
        Searches BigQuery table using VECTOR_SEARCH with Pre-filtering subquery and scalar clearance level pre-filter.
        Fails closed by raising KnowledgeStoreUnavailableError on backend failure.
        """
        if not self.bq_client:
            logger.error("BigQuery client is not initialized. Raising KnowledgeStoreUnavailableError.")
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Knowledge Store chưa được khởi tạo.")

        valid_systems = get_valid_system_filters()
        clean_system = system.upper().strip() if system else "ALL"
        if clean_system not in valid_systems:
            clean_system = "ALL"

        try:
            query_vec = self._generate_embedding(query)
            full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"

            from google.cloud import bigquery
            today_iso = datetime.date.today().isoformat()

            # Resolve effective roles and clearance
            effective_roles = user_roles
            if effective_roles is None:
                try:
                    from it_helpdesk_agent.app_utils.sso_auth import get_current_sso_user
                    user = get_current_sso_user()
                    if user:
                        effective_roles = user.roles
                except ImportError:
                    pass

            effective_clearance = user_clearance
            if effective_roles is not None:
                clean_roles = [r.lower().strip() for r in effective_roles]
                if any(r in ("admin", "it_admin", "security_admin") for r in clean_roles):
                    effective_clearance = 3
                elif any(r in ("hr_admin", "finance_admin", "compliance_officer", "legal_counsel", "hr_manager", "finance_manager") for r in clean_roles):
                    effective_clearance = 2
                elif clean_roles:
                    effective_clearance = 1
                else:
                    effective_clearance = 0
            else:
                effective_clearance = user_clearance if user_clearance > 0 else 3

            query_params = [
                bigquery.ArrayQueryParameter("query_vector", "FLOAT64", query_vec),
                bigquery.ScalarQueryParameter("limit", "INT64", limit),
                bigquery.ScalarQueryParameter("today", "DATE", today_iso),
                bigquery.ScalarQueryParameter("user_clearance", "INT64", effective_clearance),
            ]

            # 1. Construct Pre-Filter Subquery for VECTOR_SEARCH (Tombstone + Dates + System RBAC + Scalar Clearance)
            base_filters = (
                "(is_deleted IS NOT TRUE) "
                "AND (expiry_date IS NULL OR expiry_date >= @today) "
                "AND (effective_date IS NULL OR effective_date <= @today) "
                "AND (clearance_level IS NULL OR clearance_level <= @user_clearance)"
            )
            if clean_system != "ALL":
                base_table_expr = f"(SELECT * FROM {full_table} WHERE system = @system_param AND {base_filters})"
                query_params.append(bigquery.ScalarQueryParameter("system_param", "STRING", clean_system))
            elif allowed_systems is not None:
                clean_allowed = [s.upper() for s in allowed_systems if s.upper() in valid_systems and s.upper() != "ALL"]
                if not clean_allowed:
                    return []
                base_table_expr = f"(SELECT * FROM {full_table} WHERE system IN UNNEST(@allowed_systems_param) AND {base_filters})"
                query_params.append(bigquery.ArrayQueryParameter("allowed_systems_param", "STRING", clean_allowed))
            else:
                base_table_expr = f"(SELECT * FROM {full_table} WHERE {base_filters})"

            # 2. Get retrieval configuration (fraction_lists_to_search & hybrid_search_enabled)
            retrieval_cfg = get_retrieval_config()
            fraction_lists_to_search = retrieval_cfg.get("fraction_lists_to_search", 0.05)
            hybrid_enabled = retrieval_cfg.get("hybrid_search_enabled", True)

            index_active = self._is_vector_index_active()
            options_clause = f", options => '{{\"fraction_lists_to_search\": {fraction_lists_to_search}}}'" if index_active else ""

            if hybrid_enabled:
                query_params.append(bigquery.ScalarQueryParameter("query_text", "STRING", query.strip()))
                sql = f"""
                SELECT 
                    base.id, 
                    base.parent_doc_id,
                    base.chunk_index,
                    base.system, 
                    base.title, 
                    base.content, 
                    base.section_h1,
                    base.section_h2,
                    base.section_h3,
                    base.allowed_roles,
                    base.sensitivity,
                    base.clearance_level,
                    base.source_uri,
                    base.category,
                    base.keywords,
                    base.owner,
                    base.effective_date,
                    base.expiry_date,
                    base.is_deleted,
                    distance
                FROM VECTOR_SEARCH(
                    {base_table_expr},
                    'embedding',
                    query_value => @query_vector,
                    lexical_search_columns => ['title', 'content', 'keywords'],
                    lexical_search_query_value => @query_text,
                    top_k => @limit,
                    distance_type => 'COSINE'{options_clause}
                )
                ORDER BY distance ASC
                """
            else:
                # 3. Pure Vector Search SQL with BigQuery VECTOR_SEARCH Pre-Filtering & Stored Fields
                sql = f"""
                SELECT 
                    base.id, 
                    base.parent_doc_id,
                    base.chunk_index,
                    base.system, 
                    base.title, 
                    base.content, 
                    base.section_h1,
                    base.section_h2,
                    base.section_h3,
                    base.allowed_roles,
                    base.sensitivity,
                    base.clearance_level,
                    base.source_uri,
                    base.category,
                    base.keywords,
                    base.owner,
                    base.effective_date,
                    base.expiry_date,
                    base.is_deleted,
                    distance
                FROM VECTOR_SEARCH(
                    {base_table_expr},
                    'embedding',
                    query_value => @query_vector,
                    top_k => @limit,
                    distance_type => 'COSINE'{options_clause}
                )
                ORDER BY distance ASC
                """

            bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "3.0"))
            job_config = bigquery.QueryJobConfig(query_parameters=query_params)
            query_job = self.bq_client.query(sql, job_config=job_config)
            rows = query_job.result(timeout=bq_timeout)

            results = []
            for row in rows:
                content_raw = getattr(row, "content", "")
                content_str = str(content_raw) if content_raw is not None else ""
                
                dist_val = getattr(row, "distance", 0.0)
                dist_float = float(dist_val) if isinstance(dist_val, (int, float)) else 0.0
                relevance = round(max(0.0, min(1.0, 1.0 - dist_float)), 2)

                sec_h1 = _extract_str(getattr(row, "section_h1", None))
                sec_h2 = _extract_str(getattr(row, "section_h2", None))
                sec_h3 = _extract_str(getattr(row, "section_h3", None))
                
                raw_hier = getattr(row, "section_hierarchy", None)
                if raw_hier and not any([sec_h1, sec_h2, sec_h3]):
                    hier_dict = dict(raw_hier) if hasattr(raw_hier, "items") else (raw_hier if isinstance(raw_hier, dict) else {})
                    sec_h1 = _extract_str(hier_dict.get("h1"))
                    sec_h2 = _extract_str(hier_dict.get("h2"))
                    sec_h3 = _extract_str(hier_dict.get("h3"))

                sec_hier = None
                if any([sec_h1, sec_h2, sec_h3]):
                    sec_hier = SectionHierarchy(h1=sec_h1, h2=sec_h2, h3=sec_h3)

                art_id = _extract_str(getattr(row, "id", "")) or ""
                art_sys = _extract_str(getattr(row, "system", "")) or ""
                art_title = _extract_str(getattr(row, "title", "")) or ""
                category = _extract_str(getattr(row, "category", None))
                
                context_path = sec_hier.format_path() if sec_hier else f"{art_sys} > {category or 'General'} > {art_title}"
                
                is_truncated = len(content_str) > 1200
                raw_snippet = content_str[:1200].strip() + "..." if is_truncated else content_str.strip()
                snippet = wrap_retrieved_document(
                    content=raw_snippet,
                    doc_id=art_id,
                    system=art_sys,
                    title=art_title,
                )

                results.append(SearchResult(
                    article_id=art_id,
                    parent_doc_id=_extract_str(getattr(row, "parent_doc_id", None)),
                    chunk_index=getattr(row, "chunk_index", None) if isinstance(getattr(row, "chunk_index", None), int) else None,
                    system=art_sys,
                    title=art_title,
                    snippet=snippet,
                    relevance_score=relevance,
                    section_h1=sec_h1,
                    section_h2=sec_h2,
                    section_h3=sec_h3,
                    section_hierarchy=sec_hier,
                    context_path=context_path,
                    allowed_roles=_extract_list(getattr(row, "allowed_roles", None)),
                    sensitivity=_extract_str(getattr(row, "sensitivity", None)),
                    clearance_level=getattr(row, "clearance_level", None),
                    source_uri=_extract_str(getattr(row, "source_uri", None)),
                    category=category,
                    keywords=_extract_list(getattr(row, "keywords", None)),
                    owner=_extract_str(getattr(row, "owner", None)),
                    effective_date=_extract_str(getattr(row, "effective_date", None)),
                    expiry_date=_extract_str(getattr(row, "expiry_date", None)),
                    is_deleted=_extract_bool(getattr(row, "is_deleted", False)),
                    is_truncated=is_truncated,
                ))

            if retrieval_cfg.get("reranker_enabled", False) or os.getenv("USE_VERTEX_RERANKER", "false").lower() in ("true", "1", "yes"):
                results = rerank_search_results(
                    query=query,
                    candidates=results,
                    top_n=limit,
                    project_id=self.project_id,
                    ranking_model=retrieval_cfg.get("reranker_model", "semantic-ranker-512@latest"),
                )
            return results
        except Exception as e:
            logger.error("BigQuery vector search failed (%s). Raising KnowledgeStoreUnavailableError.", e)
            raise KnowledgeStoreUnavailableError(f"Truy vấn BigQuery Vector Search thất bại hoặc quá thời gian chờ: {e}") from e

    def get_article_by_id(self, article_id: str) -> Optional[KnowledgeArticle]:
        """Retrieves article by ID from BigQuery table, aggregating multi-chunk documents if present. Fails closed on failure."""
        if not self.bq_client:
            logger.error("BigQuery client is not initialized for get_article_by_id.")
            raise KnowledgeStoreUnavailableError("Dịch vụ BigQuery Knowledge Store chưa được khởi tạo.")

        clean_id = article_id.upper().strip()
        full_table = f"`{self.project_id}.{self.dataset_id}.{self.table_name}`"
        sql = f"""
        SELECT 
            id, parent_doc_id, chunk_index, system, title, category, content, keywords,
            section_h1, section_h2, section_h3, allowed_roles, sensitivity,
            source_uri, owner, effective_date, expiry_date, is_deleted, deleted_at 
        FROM {full_table} 
        WHERE UPPER(id) = @article_id 
           OR UPPER(parent_doc_id) = @article_id 
           OR UPPER(parent_doc_id) = (
               SELECT UPPER(parent_doc_id) FROM {full_table} WHERE UPPER(id) = @article_id AND parent_doc_id IS NOT NULL LIMIT 1
           )
        ORDER BY chunk_index ASC
        """
        try:
            bq_timeout = float(os.getenv("BIGQUERY_QUERY_TIMEOUT_SECONDS", "3.0"))
            from google.cloud import bigquery
            job_config = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("article_id", "STRING", clean_id)
                ]
            )
            rows = list(self.bq_client.query(sql, job_config=job_config).result(timeout=bq_timeout))
            if not rows:
                return None

            r = rows[0]
            sec_h1 = _extract_str(getattr(r, "section_h1", None))
            sec_h2 = _extract_str(getattr(r, "section_h2", None))
            sec_h3 = _extract_str(getattr(r, "section_h3", None))
            sec_hier = None
            if any([sec_h1, sec_h2, sec_h3]):
                sec_hier = SectionHierarchy(h1=sec_h1, h2=sec_h2, h3=sec_h3)

            if len(rows) > 1:
                combined_content = "\n\n".join(
                    _extract_str(getattr(row, "content", "")) or "" 
                    for row in sorted(rows, key=lambda x: getattr(x, "chunk_index", 0) or 0)
                )
            else:
                combined_content = _extract_str(getattr(r, "content", "")) or ""

            return KnowledgeArticle(
                id=_extract_str(getattr(r, "parent_doc_id", None)) or _extract_str(getattr(r, "id", "")) or "",
                parent_doc_id=_extract_str(getattr(r, "parent_doc_id", None)),
                chunk_index=0 if len(rows) > 1 else (getattr(r, "chunk_index", None) if isinstance(getattr(r, "chunk_index", None), int) else None),
                system=_extract_str(getattr(r, "system", "")) or "",
                title=_extract_str(getattr(r, "title", "")) or "",
                category=_extract_str(getattr(r, "category", None)) or "General",
                content=combined_content,
                keywords=_extract_list(getattr(r, "keywords", None)),
                section_h1=sec_h1,
                section_h2=sec_h2,
                section_h3=sec_h3,
                section_hierarchy=sec_hier,
                allowed_roles=_extract_list(getattr(r, "allowed_roles", None)),
                sensitivity=_extract_str(getattr(r, "sensitivity", None)),
                source_uri=_extract_str(getattr(r, "source_uri", None)),
                owner=_extract_str(getattr(r, "owner", None)),
                effective_date=_extract_str(getattr(r, "effective_date", None)),
                expiry_date=_extract_str(getattr(r, "expiry_date", None)),
                is_deleted=_extract_bool(getattr(r, "is_deleted", False)),
                deleted_at=_extract_str(getattr(r, "deleted_at", None)),
            )
        except Exception as e:
            logger.error("BigQuery get_article_by_id failed (%s). Raising KnowledgeStoreUnavailableError.", e)
            raise KnowledgeStoreUnavailableError(f"Truy xuất bài viết BigQuery thất bại: {e}") from e


def get_knowledge_store() -> BaseKnowledgeStore:
    """
    Factory to retrieve the appropriate Knowledge Store backend based on environment configuration.
    Supported backends:
      - 'in_memory' / 'mock' (default): In-memory keyword store for local dev & unit tests.
      - 'bigquery': BigQuery serverless vector search for cost-effective production scaling.
    """
    backend = os.getenv("KNOWLEDGE_BACKEND", "in_memory").lower()
    if backend == "bigquery":
        return BigQueryVectorKnowledgeStore()
    return InMemoryKnowledgeStore()


# Backward compatibility alias
KnowledgeStore = InMemoryKnowledgeStore
