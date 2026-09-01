import os
import re
import logging
from typing import Optional
from agent_core.app_utils.sso_auth import require_role
from agent_core.tools.obligations_store import get_obligations_store
from agent_core.tools.enterprise_rag_mcp.knowledge_store import KnowledgeStoreUnavailableError

logger = logging.getLogger(__name__)


def get_obligation(obligation_id: str) -> dict:
    """
    Tra cứu nghĩa vụ pháp lý, điều khoản hợp đồng hoặc cam kết SLA chuẩn mực (L3 Obligations Registry).
    - obligation_id: Mã định danh nghĩa vụ (ví dụ: 'OBL-SAP-001', 'OBL-DPA-001', 'OBL-SEC-001').
    Bảo vệ bởi RBAC: chỉ cho phép compliance_officer, it_admin, sys_admin, legal_counsel.
    """
    # 1. RBAC Authorization Gate
    is_allowed, error_msg = require_role(["compliance_officer", "it_admin", "sys_admin", "legal_counsel"])
    if not is_allowed:
        return {
            "status": "forbidden",
            "error": "Access Denied",
            "message": error_msg,
        }

    if not obligation_id or not str(obligation_id).strip():
        return {
            "status": "error",
            "message": "obligation_id không được để trống.",
        }

    clean_id = str(obligation_id).strip()
    try:
        store = get_obligations_store()
        ob = store.get_obligation(clean_id)
        if not ob:
            return {
                "status": "not_found",
                "obligation_id": clean_id,
                "message": f"Nghĩa vụ pháp lý '{clean_id}' không tồn tại trong cơ sở L3 Obligations Registry.",
            }
        return {
            "status": "success",
            "obligation_id": ob.obligation_id,
            "source_id": ob.source_id,
            "source_title": ob.source_title,
            "authority": ob.authority,
            "article": ob.article,
            "description": ob.description,
            "severity": ob.severity,
            "applies_to": ob.applies_to,
            "date_added": ob.date_added,
            "date_effective": ob.date_effective,
            "date_expires": ob.date_expires,
            "status_lifecycle": ob.status,
            "source_document_path": ob.source_document_path,
        }
    except KnowledgeStoreUnavailableError as e:
        logger.error("Obligations store unavailable: %s", e)
        return {
            "status": "error",
            "obligation_id": clean_id,
            "message": "Cơ sở dữ liệu L3 Obligations Registry tạm thời gián đoạn. Vui lòng thử lại sau.",
        }
    except Exception as e:
        logger.error("Error during get_obligation: %s", e)
        return {
            "status": "error",
            "obligation_id": clean_id,
            "message": f"Lỗi tra cứu nghĩa vụ: {str(e)}",
        }


def list_contract_obligations(
    source_id: Optional[str] = None,
    applies_to: Optional[str] = None,
    severity: Optional[str] = None,
    status: str = "active",
) -> dict:
    """
    Danh sách các nghĩa vụ hợp đồng/pháp lý (L3 Obligations Registry) theo bộ lọc.
    - source_id: Mã hợp đồng/chính sách nguồn (ví dụ: 'CONTRACT-SAP-ENTERPRISE-2024').
    - applies_to: 'vendor' | 'customer' | 'both'.
    - severity: 'critical' | 'high' | 'medium' | 'low'.
    - status: 'active' | 'superseded' | 'expired'.
    Bảo vệ bởi RBAC: chỉ cho phép compliance_officer, it_admin, sys_admin, legal_counsel.
    """
    is_allowed, error_msg = require_role(["compliance_officer", "it_admin", "sys_admin", "legal_counsel"])
    if not is_allowed:
        return {
            "status": "forbidden",
            "error": "Access Denied",
            "message": error_msg,
        }

    try:
        store = get_obligations_store()
        items = store.list_obligations(
            source_id=source_id,
            applies_to=applies_to,
            severity=severity,
            status=status,
        )
        return {
            "status": "success",
            "count": len(items),
            "obligations": [ob.model_dump() for ob in items],
        }
    except KnowledgeStoreUnavailableError as e:
        logger.error("Obligations store unavailable: %s", e)
        return {
            "status": "error",
            "message": "Cơ sở dữ liệu L3 Obligations Registry tạm thời gián đoạn.",
        }
    except Exception as e:
        logger.error("Error during list_contract_obligations: %s", e)
        return {
            "status": "error",
            "message": f"Lỗi liệt kê nghĩa vụ: {str(e)}",
        }


def review_it_contract_sla(
    contract_ref: Optional[str] = None,
    contract_text: Optional[str] = None,
    vendor_name: str = "IT Vendor",
    focus_area: str = "ALL"
) -> dict:
    """
    Scans IT contracts, Vendor Service Level Agreements (SLA), and Data Protection Addendums (DPA).
    Extracts uptime commitments, MTTR guarantees, penalty/credit thresholds, and data security obligations.
    Cross-references registered baseline obligations from L3 Obligations Registry.
    Supports reference-based ingestion (contract_ref) for long contract documents.
    Protected by RBAC: requires compliance_officer, it_admin, sys_admin, or legal_counsel.
    """
    # 1. RBAC Authorization Gate
    is_allowed, error_msg = require_role(["compliance_officer", "it_admin", "sys_admin", "legal_counsel"])
    if not is_allowed:
        return {
            "status": "forbidden",
            "error": "Access Denied",
            "message": error_msg,
            "vendor": vendor_name,
        }

    # 2. Resolve contract content from reference (contract_ref) or direct text (contract_text)
    content: Optional[str] = None
    if contract_ref:
        clean_path = contract_ref.replace("file://", "")
        if os.path.exists(clean_path) and os.path.isfile(clean_path):
            try:
                with open(clean_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception as e:
                return {
                    "status": "error",
                    "error": "File Read Failure",
                    "message": f"Không thể đọc hợp đồng từ tham chiếu '{contract_ref}': {e}",
                    "vendor": vendor_name,
                }
        elif "\n" in contract_ref or len(contract_ref) > 260:
            # Fallback if contract text was passed positionally as first argument
            content = contract_ref
        else:
            return {
                "status": "error",
                "error": "Contract Reference Not Found",
                "message": f"Tham chiếu hợp đồng '{contract_ref}' không tồn tại trên hệ thống lưu trữ.",
                "vendor": vendor_name,
            }
    elif contract_text:
        content = contract_text
    else:
        return {
            "status": "error",
            "error": "Missing Input",
            "message": "Vui lòng cung cấp tham chiếu hợp đồng (contract_ref) hoặc chuỗi văn bản hợp đồng (contract_text).",
            "vendor": vendor_name,
        }

    text_lower = content.lower()

    # 3. Extract SLA Uptime targets (handles prefix 'uptime: 99.9%', suffix '99.9% uptime', and inline 'cam kết 99.95% uptime')
    uptime_matches = re.findall(
        r'(?:(\d{2}(?:\.\d{1,4})?)\s*%\s*(?:uptime|availability|sẵn sàng)|(?:uptime|availability|mức độ sẵn sàng|tỉ lệ sẵn sàng)[^\n%]{0,40}?(\d{2}(?:\.\d{1,4})?)\s*%)',
        text_lower
    )
    uptime_values = []
    for m in uptime_matches:
        val = m[0] or m[1]
        if val and f"{val}% Uptime" not in uptime_values:
            uptime_values.append(f"{val}% Uptime")
    uptime_commitments = uptime_values or ["Không tìm thấy điều khoản uptime cụ thể"]

    # 4. Extract MTTR / Response / Resolution Time
    mttr_patterns = [
        # Suffix: (\d+) (unit) (action)
        r'(\d+)\s*(giờ|hours?|phút|mins?|minutes?|ngày|days?)\s*(?:để|cho)?\s*(?:phản hồi|giải quyết|khắc phục|xử lý|response|resolve|resolution|mttr)',
        # Prefix / Action: (action) ... (\d+) (unit)
        r'(?:thời gian\s*(?:phản hồi|giải quyết|khắc phục|xử lý)|response time|resolution time|resolve time|mttr|phản hồi|giải quyết|khắc phục|xử lý|resolve|response)[^\n]{0,60}?(\d+)\s*(giờ|hours?|phút|mins?|minutes?|ngày|days?)',
    ]

    mttr_commitments = []
    for pattern in mttr_patterns:
        for match in re.finditer(pattern, text_lower):
            num, unit = match.group(1), match.group(2)
            formatted = f"{num} {unit}"
            if formatted not in mttr_commitments:
                mttr_commitments.append(formatted)

    if not mttr_commitments:
        mttr_commitments = ["Chưa rõ cam kết thời gian phản hồi"]

    # 5. Analyze Security & Privacy Terms
    compliance_flags = {
        "NDA_CONFIDENTIALITY": bool(re.search(r'(bảo mật|confidentiality|non-disclosure|tiết lộ)', text_lower)),
        "DPA_DATA_PROTECTION": bool(re.search(r'(dpa|data protection|quyền riêng tư|gdpr|personal data|dữ liệu cá nhân)', text_lower)),
        "SERVICE_CREDITS_PENALTY": bool(re.search(r'(bồi thường|phạt|service credit|penalty|khấu trừ)', text_lower)),
        "AUDIT_RIGHTS": bool(re.search(r'(kiểm toán|audit rights|giám sát|thanh tra)', text_lower)),
        "DATA_BREACH_NOTIFICATION": bool(re.search(r'(thông báo sự cố|breach notification|24h|48h|72h)', text_lower)),
    }

    # 6. Cross-reference registered L3 Obligations from registry
    registered_obligations = []
    try:
        store = get_obligations_store()
        all_obs = store.list_obligations(status="active")
        v_lower = (vendor_name or "").lower()
        for ob in all_obs:
            if (
                (v_lower in ob.source_id.lower() or v_lower in ob.source_title.lower())
                or (ob.source_id.lower() in text_lower)
            ):
                registered_obligations.append(ob.model_dump())
    except Exception as e:
        logger.warning("Could not cross-reference registered obligations: %s", e)

    # 7. Identify Potential Risks
    risk_assessment = []
    if not compliance_flags["SERVICE_CREDITS_PENALTY"]:
        risk_assessment.append("RỦI RO CAO: Hợp đồng thiếu cơ chế Service Credits hoặc bồi thường tài chính khi nhà cung cấp vi phạm SLA.")
    if not compliance_flags["DATA_BREACH_NOTIFICATION"]:
        risk_assessment.append("RỦI RO BẢO MẬT: Thiếu điều khoản bắt buộc nhà cung cấp phải thông báo sự cố rò rỉ dữ liệu trong vòng 24h - 72h.")
    if not compliance_flags["AUDIT_RIGHTS"]:
        risk_assessment.append("RỦI RO TUÂN THỦ: Bên mua không có quyền yêu cầu kiểm toán an toàn thông tin độc lập định kỳ.")

    # Guardrails: Determine confidence level
    has_explicit_uptime = uptime_commitments != ["Không tìm thấy điều khoản uptime cụ thể"]
    has_explicit_mttr = mttr_commitments != ["Chưa rõ cam kết thời gian phản hồi"]
    if has_explicit_uptime and has_explicit_mttr:
        confidence_level = "HIGH"
    elif has_explicit_uptime or has_explicit_mttr:
        confidence_level = "MEDIUM"
    else:
        confidence_level = "LOW"

    return {
        "status": "success",
        "vendor": vendor_name,
        "contract_length_chars": len(content),
        "uptime_commitments": uptime_commitments,
        "mttr_commitments": mttr_commitments,
        "compliance_checklist": compliance_flags,
        "registered_contract_obligations": registered_obligations,
        "identified_legal_risks": risk_assessment or ["Hợp đồng đáp ứng đầy đủ các tiêu chuẩn an toàn cơ bản."],
        # Mandatory P0 Output Guardrails
        "confidence_level": confidence_level,
        "requires_human_review": True,
        "disclaimer": (
            "Báo cáo rà soát hợp đồng và cam kết SLA là phân tích trích xuất dữ liệu sơ bộ hỗ trợ bởi AI, "
            "KHÔNG cấu thành ý kiến tư vấn pháp lý hay kết luận ràng buộc. Mọi quyết định chế tài, khiếu nại "
            "hoặc đàm phán hợp đồng bắt buộc phải được Bộ phận Pháp chế (Legal/Compliance) xác minh và phê duyệt."
        ),
    }

