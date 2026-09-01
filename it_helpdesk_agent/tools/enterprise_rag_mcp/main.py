import os
import logging
import sys
from typing import Optional
from fastmcp import FastMCP
from dotenv import load_dotenv
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

try:
    from knowledge_store import get_knowledge_store, KnowledgeStoreUnavailableError, wrap_retrieved_document
    from rag_models import SearchResult
except ImportError:
    from it_helpdesk_agent.tools.enterprise_rag_mcp.knowledge_store import get_knowledge_store, KnowledgeStoreUnavailableError, wrap_retrieved_document
    from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import SearchResult

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
    handler = logging.StreamHandler(sys.stderr)
    logging.basicConfig(level=min_level, handlers=[handler], force=True)


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware for FastMCP HTTP / Streamable-HTTP endpoints.
    Extracts Bearer token from Authorization header, validates with SSO OIDC verifier,
    and binds current_sso_user and current_sso_raw_token ContextVars for tool execution.
    """
    async def dispatch(self, request: Request, call_next):
        # Allow health checks without auth
        if request.url.path in ("/healthz", "/health", "/readyz", "/"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            try:
                from it_helpdesk_agent.app_utils.sso_auth import ALLOW_LOCAL_DEV_SSO, SSOUser, current_sso_user
                if ALLOW_LOCAL_DEV_SSO:
                    dev_user = SSOUser(
                        user_id="dev-user-001",
                        email="dev.employee@company.com",
                        email_verified=True,
                        full_name="Local Dev Employee",
                        department="Engineering",
                        roles=["employee", "it_admin"],
                        is_authenticated=True,
                    )
                    token_ctx = current_sso_user.set(dev_user)
                    try:
                        return await call_next(request)
                    finally:
                        current_sso_user.reset(token_ctx)
            except Exception:
                pass

            return JSONResponse(
                status_code=401,
                content={"error": "Unauthorized", "detail": "Missing Authorization Bearer token header."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:].strip()
        try:
            from it_helpdesk_agent.app_utils.sso_auth import verify_sso_token, current_sso_user, current_sso_raw_token
            user = verify_sso_token(token)
            token_ctx = current_sso_user.set(user)
            raw_token_ctx = current_sso_raw_token.set(token)
            try:
                return await call_next(request)
            finally:
                current_sso_user.reset(token_ctx)
                current_sso_raw_token.reset(raw_token_ctx)
        except Exception as exc:
            logger.warning("MCP OIDC/SSO authentication failed: %s", exc)
            detail = getattr(exc, "detail", str(exc))
            status_code = getattr(exc, "status_code", 401)
            return JSONResponse(
                status_code=status_code,
                content={"error": "Unauthorized", "detail": f"SSO Authentication failed: {detail}"},
                headers={"WWW-Authenticate": "Bearer"},
            )


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

    try:
        from it_helpdesk_agent.app_utils.sso_auth import get_current_sso_user
    except ImportError:
        try:
            from app_utils.sso_auth import get_current_sso_user
        except ImportError:
            def get_current_sso_user():
                return None

    current_user = get_current_sso_user()
    user_roles = current_user.roles if current_user else None

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
            results = store.search(query=query, system=clean_sys, limit=3, user_roles=user_roles)
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
            allowed_systems=authorized_systems,
            user_roles=user_roles,
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
    Enforces domain-level and document-level RBAC for sensitive enterprise system operational guides.
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

    # Document-level RBAC & Sensitivity trimming
    try:
        from it_helpdesk_agent.app_utils.sso_auth import get_current_sso_user
    except ImportError:
        try:
            from app_utils.sso_auth import get_current_sso_user
        except ImportError:
            def get_current_sso_user():
                return None

    current_user = get_current_sso_user()
    current_roles = [r.lower().strip() for r in (current_user.roles if current_user else ["employee"])]
    is_admin = any(r in ("admin", "it_admin", "security_admin") for r in current_roles)

    doc_allowed = [r.lower().strip() for r in (getattr(article, "allowed_roles", None) or [])]
    if doc_allowed and not is_admin:
        if not any(r in current_roles for r in doc_allowed):
            return {
                "status": "forbidden",
                "error": "Access Denied",
                "message": f"Tài liệu '{article_id}' yêu cầu quyền truy cập đặc biệt: {article.allowed_roles}.",
                "article_id": article_id,
                "system": article.system,
            }
    doc_sens = (getattr(article, "sensitivity", "INTERNAL") or "INTERNAL").upper()
    if doc_sens in ("CONFIDENTIAL", "RESTRICTED") and not is_admin:
        if not any(r in current_roles for r in doc_allowed) and not any(r in ("hr_admin", "finance_admin", "security_admin") for r in current_roles):
            return {
                "status": "forbidden",
                "error": "Access Denied",
                "message": f"Tài liệu '{article_id}' có mức độ bảo mật {article.sensitivity}, bạn không có quyền truy cập.",
                "article_id": article_id,
                "system": article.system,
            }

    art_dict = article.model_dump()
    raw_content = art_dict.get("content", "")
    art_dict["content"] = wrap_retrieved_document(
        content=raw_content,
        doc_id=article.id,
        system=article.system,
        title=article.title,
    )
    return {"status": "success", "article": art_dict}


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


def get_mcp_app():
    """Returns Starlette ASGI app for Streamable-HTTP MCP serving."""
    from starlette.middleware import Middleware
    return mcp.http_app(
        transport="streamable-http",
        middleware=[Middleware(MCPAuthMiddleware)]
    )


if __name__ == "__main__":
    load_dotenv()
    _initialize_console_logging()
    transport = os.getenv("MCP_TRANSPORT", "streamable-http").lower()
    host = os.getenv("MCP_HOST", "0.0.0.0")
    port = int(os.getenv("MCP_PORT", "8001"))

    if transport in ("streamable-http", "http"):
        from starlette.middleware import Middleware
        mcp.run(
            transport="streamable-http",
            host=host,
            port=port,
            middleware=[Middleware(MCPAuthMiddleware)]
        )
    else:
        mcp.run(transport="stdio")
