"""
Rule-based Triage Fast-Path Classifier.

Provides deterministic regex & keyword matching for enterprise queries,
routing them directly to the appropriate operational tier (L1, L2, L3) before the LLM loop.
Leverages domain keywords configured in active domain pack systems.yaml with robust fallbacks.
"""

import re
from typing import Optional, Dict, Any

# Precompiled fast-path rules fallback
L3_PATTERNS = [
    r"(?i)\b(stack\s*trace|traceback|out\s*of\s*memory|oomkiller|nullpointerexception|segmentation\s*fault|fatal\s*error|core\s*dump)\b",
    r"(?i)\b(root\s*cause\s*analysis|rca|phân\s*tích\s*log|rà\s*soát\s*hợp\s*đồng|review\s*contract|điều\s*khoản\s*sla|service\s*credits)\b",
    r"(?i)\b(deadlock|database\s*crash|sập\s*hệ\s*thống|down\s*time|502\s*bad\s*gateway|504\s*gateway\s*timeout)\b",
]

L2_PATTERNS = [
    r"(?i)\b(sap|me21n|va01|migo|f-02|fb50|bapi|tcode|trans\s*code|kỳ\s*kế\s*toán|hóa\s*đơn\s*điện\s*tử|purchase\s*order|đơn\s*mua\s*hàng)\b",
    r"(?i)\b(workday|bamboohr|bảng\s*lương|payroll|chấm\s*công\s*vân\s*tay|timesheet|onboarding|offboarding)\b",
    r"(?i)\b(salesforce|hubspot|crm|lead\s*conversion|opportunity|account\s*owner|quota\s*limit)\b",
]

L1_PATTERNS = [
    r"(?i)\b(quên\s*mật\s*khẩu|reset\s*password|đổi\s*mật\s*khẩu|mở\s*khóa\s*tài\s*khoản|unlock\s*account|mfa|2fa|otp|active\s*directory)\b",
    r"(?i)\b(kết\s*nối\s*wifi|wi-fi|mạng\s*văn\s*phòng|cài\s*máy\s*in|printer\s*setup|cài\s*đặt\s*vpn|vpn\s*setup|hướng\s*dẫn\s*vpn)\b",
    r"(?i)\b(tra\s*cứu\s*ticket|tạo\s*ticket|xem\s*ticket|ticket\s*status|ticket\s*id|hotline\s*it|giờ\s*làm\s*việc\s*it)\b",
]


def classify_intent_fast_path(query: str) -> Optional[Dict[str, Any]]:
    """
    Classifies user intent using deterministic high-confidence regex rules.
    Prioritizes dynamic patterns from active domain pack systems.yaml.
    Returns a dict with target_agent, tier, confidence, and rule match details, or None if ambiguous.
    """
    if not query or not query.strip():
        return None

    clean_q = query.strip()

    # Try dynamic patterns from system config first
    try:
        from agent_core.app_utils.system_config import get_domain_keyword_patterns
        dyn_patterns = get_domain_keyword_patterns()
        
        # 1. Check L3 Diagnostics
        l3_pat = dyn_patterns.get("L3_DIAGNOSTICS")
        if l3_pat:
            m = l3_pat.search(clean_q)
            if m:
                return {
                    "target_agent": "l3_deep_diagnostics_agent",
                    "tier": "L3",
                    "confidence": 0.99,
                    "matched_pattern": m.group(0),
                    "reason": "Dynamic match on L3 diagnostics keyword",
                }
        
        # 2. Check L2 Enterprise systems (ERP, HRM, CRM)
        for domain in ("ERP", "HRM", "CRM"):
            d_pat = dyn_patterns.get(domain)
            if d_pat:
                m = d_pat.search(clean_q)
                if m:
                    return {
                        "target_agent": "l2_enterprise_rag_agent",
                        "tier": "L2",
                        "confidence": 0.95,
                        "matched_pattern": m.group(0),
                        "reason": f"Dynamic match on {domain} enterprise system keyword",
                    }
    except Exception:
        pass

    # Static fallback rules
    for p in L3_PATTERNS:
        match = re.search(p, clean_q)
        if match:
            return {
                "target_agent": "l3_deep_diagnostics_agent",
                "tier": "L3",
                "confidence": 0.99,
                "matched_pattern": match.group(0),
                "reason": "Deterministic match on diagnostic log/RCA/SLA keyword",
            }

    for p in L2_PATTERNS:
        match = re.search(p, clean_q)
        if match:
            return {
                "target_agent": "l2_enterprise_rag_agent",
                "tier": "L2",
                "confidence": 0.95,
                "matched_pattern": match.group(0),
                "reason": "Deterministic match on Enterprise System (ERP/HRM/CRM) keyword",
            }

    for p in L1_PATTERNS:
        match = re.search(p, clean_q)
        if match:
            return {
                "target_agent": "l1_selfservice_agent",
                "tier": "L1",
                "confidence": 0.95,
                "matched_pattern": match.group(0),
                "reason": "Deterministic match on IT Self-Service / FAQ keyword",
            }

    return None
