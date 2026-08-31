import logging
import sys
from typing import Literal, Optional
from fastmcp import FastMCP
from dotenv import load_dotenv

try:
    from knowledge_store import get_knowledge_store
    from rag_models import SearchResult, DocumentSummary
except ImportError:
    from it_helpdesk_agent.tools.enterprise_rag_mcp.knowledge_store import get_knowledge_store
    from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import SearchResult, DocumentSummary

# Common administrative and IT support roles authorized across all enterprise domains
ADMIN_SUPPORT_ROLES = [
    "it_admin", "sys_admin", "admin", "support_agent", "helpdesk_operator", "lead_engineer"
]

# Domain-specific RBAC roles for Enterprise RAG systems
SYSTEM_REQUIRED_ROLES = {
    "HRM": ["hr_specialist", "hr_manager", "payroll_admin", "hr_operations", *ADMIN_SUPPORT_ROLES],
    "ERP": ["erp_user", "finance_user", "accountant", "procurement_specialist", "procurement_manager", *ADMIN_SUPPORT_ROLES],
    "CRM": ["sales_rep", "sales_manager", "marketing", "crm_admin", *ADMIN_SUPPORT_ROLES],
}


def _check_system_access(system: str) -> tuple[bool, Optional[str]]:
    """
    Verifies if the current authenticated caller is authorized to access documentation for the specified system.
    Fails closed if SSO authorization layer cannot be resolved.
    """
    sys_upper = system.upper()
    if sys_upper == "ALL":
        return True, None

    needed_roles = SYSTEM_REQUIRED_ROLES.get(sys_upper)
    if not needed_roles:
        return True, None

    try:
        from it_helpdesk_agent.app_utils.sso_auth import require_role
    except ImportError:
        try:
            from app_utils.sso_auth import require_role
        except ImportError:
            return False, "Hệ thống xác thực SSO không khả dụng (ImportError). Truy cập bị từ chối theo nguyên tắc Fail-Closed."

    return require_role(needed_roles)


def _get_authorized_systems() -> list[str]:
    """
    Returns the list of enterprise systems the current user is authorized to access.
    """
    authorized = []
    for sys_name in ("ERP", "HRM", "CRM"):
        allowed, _ = _check_system_access(sys_name)
        if allowed:
            authorized.append(sys_name)
    return authorized


def _initialize_console_logging(min_level: int = logging.INFO):
    # Logs MUST go to stderr to prevent breaking the stdio MCP protocol
    handler = logging.StreamHandler(sys.stderr)
    logging.basicConfig(level=min_level, handlers=[handler], force=True)

store = get_knowledge_store()
mcp = FastMCP(name="EnterpriseKnowledgeRAG")

@mcp.tool()
def search_enterprise_knowledge(
    query: str,
    system: Literal["ERP", "HRM", "CRM", "ALL"] = "ALL"
) -> list[dict]:
    """
    Searches enterprise knowledge base for ERP, HRM, and CRM systems.
    - system: 'ERP', 'HRM', 'CRM', or 'ALL'
    Enforces domain-level RBAC authorization and Pre-Query Security Trimming based on authenticated user roles.
    """
    if system != "ALL":
        is_allowed, error_msg = _check_system_access(system)
        if not is_allowed:
            return [{
                "article_id": f"{system}-FORBIDDEN",
                "title": f"Access Denied: Restricted {system} System Documentation",
                "snippet": error_msg or f"Truy cập tài liệu {system} bị từ chối do không đủ quyền hạn.",
                "system": system,
                "score": 0.0,
            }]
        results = store.search(query=query, system=system, limit=3)
        return [r.model_dump() for r in results]

    # Pre-query Security Trimming for system == "ALL":
    # Calculate authorized systems before querying database to avoid pulling restricted records into memory
    # and to ensure vector search top_k slots are filled exclusively with accessible documents.
    authorized_systems = _get_authorized_systems()
    if not authorized_systems:
        return []

    results = store.search(
        query=query,
        system="ALL",
        limit=3,
        allowed_systems=authorized_systems
    )
    return [r.model_dump() for r in results]


@mcp.tool()
def get_system_manual(article_id: str) -> dict:
    """
    Retrieves the complete technical manual or troubleshooting guide for a specific article ID.
    Enforces domain-level RBAC for sensitive HRM, ERP, and CRM operational guides.
    """
    article = store.get_article_by_id(article_id)
    if not article:
        return {"status": "error", "message": f"Article '{article_id}' not found."}
    
    is_allowed, error_msg = _check_system_access(article.system)
    if not is_allowed:
        return {
            "status": "forbidden",
            "error": "Access Denied",
            "message": error_msg,
            "article_id": article_id,
            "system": article.system,
        }

    return {"status": "success", "article": article.model_dump()}

@mcp.tool()
def summarize_long_document(document_text: str, system_name: str = "Enterprise System") -> dict:
    """
    Extracts key points, prerequisites, and action items from long enterprise documents.
    """
    lines = [line.strip() for line in document_text.strip().split("\n") if line.strip()]
    key_points = [l for l in lines if l.startswith(("-", "*", "1.", "2.", "3.", "4.", "5."))] or lines[:3]
    action_items = [l for l in lines if any(w in l.lower() for w in ["bước", "step", "yêu cầu", "khắc phục", "action", "quy trình"])]
    
    return {
        "status": "success",
        "system": system_name,
        "summary": {
            "total_length": len(document_text),
            "key_points": key_points[:5],
            "action_items": action_items[:5] or ["Tuân thủ quy trình bảo mật IT."],
        }
    }

@mcp.tool()
def draft_email_response(
    user_name: str,
    ticket_id: str,
    issue_summary: str,
    solution_steps: str,
    urgency: str = "Normal"
) -> dict:
    """
    Drafts a standardized, polite, and professional email response to update the user regarding their ticket.
    """
    email_subject = f"[IT Helpdesk - {ticket_id}] Cập nhật xử lý: {issue_summary}"
    email_body = f"""Kính gửi Anh/Chị {user_name},

Bộ phận IT Helpdesk xin thông báo về tiến độ xử lý yêu cầu hỗ trợ của Anh/Chị:
- Mã Ticket: {ticket_id}
- Vấn đề ghi nhận: {issue_summary}
- Mức độ ưu tiên: {urgency}

--- HƯỚNG DẪN XỬ LÝ / KẾT QUẢ ---
{solution_steps}

Nếu Anh/Chị cần hỗ trợ thêm hoặc sự cố chưa được giải quyết triệt để, vui lòng phản hồi trực tiếp email này hoặc liên hệ hotline IT Helpdesk (Ext: 1080).

Trân trọng,
Đội ngũ IT Helpdesk & Enterprise Support
"""
    return {
        "status": "success",
        "subject": email_subject,
        "body": email_body
    }

if __name__ == "__main__":
    load_dotenv()
    _initialize_console_logging()
    mcp.run(transport="stdio")
