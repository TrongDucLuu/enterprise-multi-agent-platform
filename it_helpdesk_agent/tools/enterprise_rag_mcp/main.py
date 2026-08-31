import logging
import sys
from typing import Optional
from fastmcp import FastMCP
from dotenv import load_dotenv

try:
    from knowledge_store import get_knowledge_store, KnowledgeStoreUnavailableError
    from rag_models import SearchResult, DocumentSummary
except ImportError:
    from it_helpdesk_agent.tools.enterprise_rag_mcp.knowledge_store import get_knowledge_store, KnowledgeStoreUnavailableError
    from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import SearchResult, DocumentSummary

try:
    from it_helpdesk_agent.app_utils.system_config import (
        get_configured_systems,
        get_valid_system_filters,
        get_system_required_roles,
    )
except ImportError:
    try:
        from app_utils.system_config import (
            get_configured_systems,
            get_valid_system_filters,
            get_system_required_roles,
        )
    except ImportError:
        def get_configured_systems() -> list[str]:
            return ["ERP", "HRM", "CRM"]
        def get_valid_system_filters() -> set[str]:
            return {"ERP", "HRM", "CRM", "ALL"}
        def get_system_required_roles(system: str) -> list[str]:
            return ["it_admin", "admin"]


logger = logging.getLogger(__name__)


def _check_system_access(system: str) -> tuple[bool, Optional[str]]:
    """
    Verifies if the current authenticated caller is authorized to access documentation for the specified system.
    Fails closed if SSO authorization layer cannot be resolved.
    """
    sys_upper = system.upper().strip()
    if sys_upper == "ALL":
        return True, None

    needed_roles = get_system_required_roles(sys_upper)
    if not needed_roles:
        # Check if the system is known in configuration
        configured = get_configured_systems()
        if sys_upper not in configured:
            return False, f"Hệ thống '{system}' không được định nghĩa trong cấu hình doanh nghiệp (Fail-Closed)."
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
    Dynamically resolves systems from configuration.
    """
    authorized = []
    for sys_name in get_configured_systems():
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
    system: str = "ALL"
) -> list[dict]:
    """
    Searches enterprise knowledge base for technical manuals and troubleshooting procedures.
    - query: Keyword or natural language question regarding an enterprise system issue.
    - system: System identifier (e.g. 'ERP', 'HRM', 'CRM', or 'ALL' to search all authorized systems).
    Enforces domain-level RBAC authorization and Pre-Query Security Trimming based on authenticated user roles.
    """
    clean_sys = system.upper().strip() if system else "ALL"
    valid_systems = get_valid_system_filters()

    # Explicit input boundary validation
    if clean_sys not in valid_systems:
        valid_names = ", ".join(sorted(list(get_configured_systems())))
        return [{
            "article_id": "INVALID-SYSTEM",
            "title": f"Invalid System Specified: '{system}'",
            "snippet": f"Hệ thống '{system}' không hợp lệ. Các hệ thống được hỗ trợ bao gồm: {valid_names}, hoặc 'ALL'.",
            "system": system,
            "score": 0.0,
        }]

    if clean_sys != "ALL":
        is_allowed, error_msg = _check_system_access(clean_sys)
        if not is_allowed:
            return [{
                "article_id": f"{clean_sys}-FORBIDDEN",
                "title": f"Access Denied: Restricted {clean_sys} System Documentation",
                "snippet": error_msg or f"Truy cập tài liệu {clean_sys} bị từ chối do không đủ quyền hạn.",
                "system": clean_sys,
                "score": 0.0,
            }]
        try:
            results = store.search(query=query, system=clean_sys, limit=3)
            return [r.model_dump() for r in results]
        except KnowledgeStoreUnavailableError as e:
            logger.error("Knowledge store unavailable during search: %s", e)
            return [{
                "article_id": "STORE-UNAVAILABLE",
                "title": "Dịch vụ Tra cứu Tri thức Tạm thời Gián đoạn",
                "snippet": "Cơ sở dữ liệu tri thức doanh nghiệp hiện không phản hồi. Vui lòng thử lại sau.",
                "system": clean_sys,
                "score": 0.0,
            }]

    # Pre-query Security Trimming for system == "ALL":
    authorized_systems = _get_authorized_systems()
    if not authorized_systems:
        return []

    try:
        results = store.search(
            query=query,
            system="ALL",
            limit=3,
            allowed_systems=authorized_systems
        )
        return [r.model_dump() for r in results]
    except KnowledgeStoreUnavailableError as e:
        logger.error("Knowledge store unavailable during search ALL: %s", e)
        return [{
            "article_id": "STORE-UNAVAILABLE",
            "title": "Dịch vụ Tra cứu Tri thức Tạm thời Gián đoạn",
            "snippet": "Cơ sở dữ liệu tri thức doanh nghiệp hiện không phản hồi. Vui lòng thử lại sau.",
            "system": "ALL",
            "score": 0.0,
        }]


@mcp.tool()
def get_system_manual(article_id: str) -> dict:
    """
    Retrieves the complete technical manual or troubleshooting guide for a specific article ID.
    Enforces domain-level RBAC for sensitive enterprise system operational guides.
    """
    try:
        article = store.get_article_by_id(article_id)
    except KnowledgeStoreUnavailableError as e:
        logger.error("Knowledge store unavailable during get_system_manual: %s", e)
        return {
            "status": "error",
            "error_code": "KNOWLEDGE_STORE_UNAVAILABLE",
            "message": "Dịch vụ cơ sở dữ liệu tri thức tạm thời gián đoạn. Vui lòng thử lại sau.",
            "article_id": article_id
        }

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
