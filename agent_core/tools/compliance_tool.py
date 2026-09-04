import os
import re
import logging
from typing import Optional, Any
from agent_core.app_utils.sso_auth import require_role
from agent_core.tools.obligations_store import get_obligations_store
from agent_core.tools.enterprise_rag_mcp.knowledge_store import KnowledgeStoreUnavailableError
from agent_core.tools.registry import register_tool
from agent_core.app_utils.semantic_cache import record_source_clearance

logger = logging.getLogger(__name__)


@register_tool("get_obligation")
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

        if hasattr(ob, "clearance_level"):
            record_source_clearance(getattr(ob, "clearance_level", 0))

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


@register_tool("list_contract_obligations")
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


def _extract_snippets_with_offsets(
    content: str,
    patterns: list[str],
    category: str,
    context_window: int = 50,
) -> list[dict[str, Any]]:
    """
    Helper function extracting matching regex occurrences with exact character offsets
    and local context snippets from contract text.
    """
    matches = []
    for pattern in patterns:
        for match in re.finditer(pattern, content, flags=re.IGNORECASE):
            start = match.start()
            end = match.end()
            matched_text = match.group(0).strip()
            
            snippet_start = max(0, start - context_window)
            snippet_end = min(len(content), end + context_window)
            context_snippet = content[snippet_start:snippet_end].strip()

            matches.append({
                "category": category,
                "matched_text": matched_text,
                "char_offset": start,
                "end_offset": end,
                "context_snippet": context_snippet,
            })
    return matches


@register_tool("review_contract_sla")
def review_contract_sla(
    contract_text: Optional[str] = None,
    vendor_name: str = "IT Vendor",
    focus_area: str = "ALL",
    contract_ref: Optional[str] = None,
) -> dict:
    """
    Pre-screens IT contracts, Vendor Service Level Agreements (SLA), and Data Protection Addendums (DPA).
    Extracts exact matching keywords, clauses, uptime commitments, MTTR guarantees, and security terms
    along with precise character offsets and context snippets.
    Cross-references registered baseline obligations from L3 Obligations Registry.
    Requires compliance_officer, it_admin, sys_admin, or legal_counsel.
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

    # 2. Resolve contract content safely from Cloud Storage reference (contract_ref) or direct text (contract_text)
    from agent_core.app_utils.artifact_storage import resolve_artifact_content
    effective_ref = contract_ref
    effective_raw = contract_text
    if effective_raw and not effective_ref and effective_raw.strip().startswith("gs://"):
        effective_ref = effective_raw.strip()
        effective_raw = None

    content, err = resolve_artifact_content(ref=effective_ref, raw_text=effective_raw, resource_label="hợp đồng")
    if err is not None:
        err["vendor"] = vendor_name
        return err

    # 3. Extract SLA Uptime targets with character offsets
    uptime_patterns = [
        r'(?:(\d{2}(?:\.\d{1,4})?)\s*%\s*(?:uptime|availability|sẵn sàng)|(?:uptime|availability|mức độ sẵn sàng|tỉ lệ sẵn sàng)[^\n%]{0,40}?(\d{2}(?:\.\d{1,4})?)\s*%)',
    ]
    uptime_matches = _extract_snippets_with_offsets(content, uptime_patterns, "UPTIME_SLA")
    uptime_commitments = []
    for m in uptime_matches:
        val_match = re.search(r'(\d{2}(?:\.\d{1,4})?)\s*%', m["matched_text"])
        if val_match:
            formatted = f"{val_match.group(1)}% Uptime"
            if formatted not in uptime_commitments:
                uptime_commitments.append(formatted)

    # 4. Extract MTTR / Response / Resolution Time with character offsets
    mttr_patterns = [
        r'(\d+)\s*(giờ|hours?|phút|mins?|minutes?|ngày|days?)\s*(?:để|cho)?\s*(?:phản hồi|giải quyết|khắc phục|xử lý|response|resolve|resolution|mttr)',
        r'(?:thời gian\s*(?:phản hồi|giải quyết|khắc phục|xử lý)|response time|resolution time|resolve time|mttr|phản hồi|giải quyết|khắc phục|xử lý|resolve|response)[^\n]{0,60}?(\d+)\s*(giờ|hours?|phút|mins?|minutes?|ngày|days?)',
    ]
    mttr_matches = _extract_snippets_with_offsets(content, mttr_patterns, "MTTR_SLA")
    mttr_commitments = []
    for m in mttr_matches:
        num_unit_match = re.search(r'(\d+)\s*(giờ|hours?|phút|mins?|minutes?|ngày|days?)', m["matched_text"], re.IGNORECASE)
        if num_unit_match:
            formatted = f"{num_unit_match.group(1)} {num_unit_match.group(2).lower()}"
            if formatted not in mttr_commitments:
                mttr_commitments.append(formatted)

    # 5. Extract Specific Contract Clause Matches with exact character offsets
    # Note on DATA_BREACH_NOTIFICATION: strictly requires breach notification context,
    # NOT matching standalone "24h" or "48h" response times.
    clause_definitions = {
        "NDA_CONFIDENTIALITY": [
            r'\b(?:bảo mật\s*thông\s*tin|confidentiality|non-disclosure|thỏa thuận bảo mật)\b',
        ],
        "DPA_DATA_PROTECTION": [
            r'\b(?:dpa|data\s*protection\s*addendum|quyền\s*riêng\s*tư|gdpr|personal\s*data|dữ\s*liệu\s*cá\s*nhân)\b',
        ],
        "SERVICE_CREDITS_PENALTY": [
            r'\b(?:bồi\s*thường|phạt\s*vi\s*phạm|service\s*credits?|penalty|khấu\s*trừ\s*phí)\b',
        ],
        "AUDIT_RIGHTS": [
            r'\b(?:kiểm\s*toán|audit\s*rights?|giám\s*sát\s*an\s*toàn|thanh\s*tra\s*định\s*kỳ)\b',
        ],
        "DATA_BREACH_NOTIFICATION": [
            r'(?:thông\s*báo\s*(?:sự\s*cố|rò\s*rỉ|vi\s*phạm\s*dữ\s*liệu)|breach\s*notification|data\s*breach\s*(?:reporting|notification))',
            r'(?:sự\s*cố\s*bảo\s*mật|rò\s*rỉ\s*dữ\s*liệu)[^\n]{0,50}?(?:trong\s*vòng|within)\s*\d+\s*(?:h|giờ|hours?)',
        ],
    }

    all_matched_clauses: list[dict[str, Any]] = []
    all_matched_clauses.extend(uptime_matches)
    all_matched_clauses.extend(mttr_matches)

    checklist_matches: dict[str, bool] = {}
    for cat_name, cat_patterns in clause_definitions.items():
        cat_matches = _extract_snippets_with_offsets(content, cat_patterns, cat_name)
        checklist_matches[cat_name] = len(cat_matches) > 0
        all_matched_clauses.extend(cat_matches)

    # 6. Cross-reference registered L3 Obligations from registry
    registered_obligations: list[dict[str, Any]] = []
    try:
        store = get_obligations_store()
        all_obs = store.list_obligations(status="active")
        v_lower = (vendor_name or "").lower()
        content_lower = content.lower()
        for ob in all_obs:
            if (
                (v_lower in ob.source_id.lower() or v_lower in ob.source_title.lower())
                or (ob.source_id.lower() in content_lower)
            ):
                registered_obligations.append(ob.model_dump())
    except Exception as e:
        logger.warning("Could not cross-reference registered obligations: %s", e)

    # 7. Structured Findings with Mandatory Grounded Citations (quote & char_offset)
    structured_findings: list[dict[str, Any]] = []
    for match in all_matched_clauses:
        structured_findings.append({
            "category": match["category"],
            "status": "compliant",
            "quote": match["matched_text"],
            "char_offset": match["char_offset"],
            "end_offset": match["end_offset"],
            "context_snippet": match["context_snippet"],
            "description": f"Tìm thấy điều khoản '{match['category']}' tại vị trí ký tự {match['char_offset']}.",
        })

    # Flag missing items ONLY when they are registered obligations in L3 Obligations Registry
    missing_registered_obligations: list[dict[str, Any]] = []
    for ob in registered_obligations:
        ob_id = ob.get("obligation_id", "")
        ob_desc = ob.get("description", "")
        # Check if text contains evidence of this specific obligation
        has_citation = any(
            (ob_id.lower() in m["matched_text"].lower()) or (ob.get("article", "").lower() in m["matched_text"].lower())
            for m in all_matched_clauses if m.get("matched_text")
        )
        if not has_citation:
            missing_registered_obligations.append({
                "category": ob.get("applies_to", "OBLIGATION"),
                "status": "missing",
                "quote": "",
                "char_offset": None,
                "obligation_id": ob_id,
                "description": f"Thiếu điều khoản theo nghĩa vụ L3 đã đăng ký ({ob_id}): {ob_desc}",
            })

    # Confidence level based strictly on number and validity of verified citations
    valid_citations = [f for f in structured_findings if f["char_offset"] is not None and f["quote"]]
    if len(valid_citations) >= 4:
        confidence_level = "HIGH"
    elif len(valid_citations) >= 1:
        confidence_level = "MEDIUM"
    else:
        confidence_level = "LOW"

    # Summary of identified legal notes
    legal_notes = []
    if missing_registered_obligations:
        for m in missing_registered_obligations:
            legal_notes.append(f"CẢNH BÁO NGHĨA VỤ ĐÃ ĐĂNG KÝ: {m['description']}")
    elif valid_citations:
        legal_notes.append("Hợp đồng có các trích dẫn điều khoản SLA và an toàn thông tin cơ bản.")
    else:
        legal_notes.append("Không tìm thấy trích dẫn điều khoản hợp đồng hoặc SLA cụ thể trong văn bản.")

    return {
        "status": "success",
        "vendor": vendor_name,
        "contract_length_chars": len(content),
        "uptime_commitments": uptime_commitments or ["Không tìm thấy điều khoản uptime cụ thể"],
        "mttr_commitments": mttr_commitments or ["Chưa rõ cam kết thời gian phản hồi"],
        "compliance_checklist": checklist_matches,
        "matched_clauses": all_matched_clauses,
        "structured_findings": structured_findings,
        "missing_registered_obligations": missing_registered_obligations,
        "registered_contract_obligations": registered_obligations,
        "identified_legal_risks": legal_notes,
        "confidence_level": confidence_level,
        "requires_human_review": True,
        "disclaimer": (
            "Báo cáo rà soát hợp đồng và tiền kiểm từ khóa là phân tích trích xuất dữ liệu sơ bộ hỗ trợ bởi AI, "
            "KHÔNG cấu thành ý kiến tư vấn pháp lý hay kết luận ràng buộc. Mọi đánh giá rủi ro, quyết định chế tài, "
            "khiếu nại hoặc đàm phán hợp đồng bắt buộc phải được Bộ phận Pháp chế (Legal/Compliance) xác minh và phê duyệt trực tiếp."
        ),
    }


# Backwards compatibility and descriptive aliases
prescreen_contract_keywords = review_contract_sla
review_it_contract_sla = review_contract_sla


