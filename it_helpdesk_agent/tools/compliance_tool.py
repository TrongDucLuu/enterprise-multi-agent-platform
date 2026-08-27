import re
from typing import Optional

def review_it_contract_sla(
    contract_text: str,
    vendor_name: str = "IT Vendor",
    focus_area: str = "ALL"
) -> dict:
    """
    Scans IT contracts, Vendor Service Level Agreements (SLA), and Data Protection Addendums (DPA).
    Extracts uptime commitments, MTTR guarantees, penalty/credit thresholds, and data security obligations.
    """
    text_lower = contract_text.lower()

    # Extract SLA Uptime targets
    uptime_matches = re.findall(r'(\d{2}(?:\.\d{1,4})?)\s*%\s*(?:uptime|availability|sẵn sàng)', text_lower)
    uptime_commitments = [f"{m}% Uptime" for m in uptime_matches] or ["Không tìm thấy điều khoản uptime cụ thể"]

    # Extract MTTR (Mean Time to Resolve) / Response Time
    response_time_matches = re.findall(r'(\d+)\s*(?:giờ|hours|phút|minutes)\s*(?:response|phản hồi|giải quyết|resolve)', text_lower)
    mttr_commitments = [f"{m} đơn vị thời gian" for m in response_time_matches] or ["Chưa rõ cam kết thời gian phản hồi"]

    # Analyze Security & Privacy Terms
    compliance_flags = {
        "NDA_CONFIDENTIALITY": bool(re.search(r'(bảo mật|confidentiality|non-disclosure|tiết lộ)', text_lower)),
        "DPA_DATA_PROTECTION": bool(re.search(r'(dpa|data protection|quyền riêng tư|gdpr|personal data|dữ liệu cá nhân)', text_lower)),
        "SERVICE_CREDITS_PENALTY": bool(re.search(r'(bồi thường|phạt|service credit|penalty|khấu trừ)', text_lower)),
        "AUDIT_RIGHTS": bool(re.search(r'(kiểm toán|audit rights|giám sát|thanh tra)', text_lower)),
        "DATA_BREACH_NOTIFICATION": bool(re.search(r'(thông báo sự cố|breach notification|24h|48h|72h)', text_lower)),
    }

    # Identify Potential Risks
    risk_assessment = []
    if not compliance_flags["SERVICE_CREDITS_PENALTY"]:
        risk_assessment.append("RỦI RO CAO: Hợp đồng thiếu cơ chế Service Credits hoặc bồi thường tài chính khi nhà cung cấp vi phạm SLA.")
    if not compliance_flags["DATA_BREACH_NOTIFICATION"]:
        risk_assessment.append("RỦI RO BẢO MẬT: Thiếu điều khoản bắt buộc nhà cung cấp phải thông báo sự cố rò rỉ dữ liệu trong vòng 24h - 72h.")
    if not compliance_flags["AUDIT_RIGHTS"]:
        risk_assessment.append("RỦI RO TUÂN THỦ: Bên mua không có quyền yêu cầu kiểm toán an toàn thông tin độc lập định kỳ.")

    return {
        "status": "success",
        "vendor": vendor_name,
        "contract_length_chars": len(contract_text),
        "uptime_commitments": uptime_commitments,
        "mttr_commitments": mttr_commitments,
        "compliance_checklist": compliance_flags,
        "identified_legal_risks": risk_assessment or ["Hợp đồng đáp ứng đầy đủ các tiêu chuẩn an toàn cơ bản."]
    }
