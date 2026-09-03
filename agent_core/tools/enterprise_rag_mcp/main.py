import os
import logging
import sys
from typing import Optional, Any
from fastmcp import FastMCP
from dotenv import load_dotenv
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

try:
    from knowledge_store import get_knowledge_store, get_facts_store, KnowledgeStoreUnavailableError, wrap_retrieved_document
    from rag_models import SearchResult, Fact, Obligation, SecurityContext
    from obligations_store import get_obligations_store
except ImportError:
    from agent_core.tools.enterprise_rag_mcp.knowledge_store import get_knowledge_store, get_facts_store, KnowledgeStoreUnavailableError, wrap_retrieved_document
    from agent_core.tools.enterprise_rag_mcp.rag_models import SearchResult, Fact, Obligation, SecurityContext
    from agent_core.tools.obligations_store import get_obligations_store

try:
    from agent_core.knowledge.authorize import authorize_document
except ImportError:
    from knowledge.authorize import authorize_document

try:
    from agent_core.app_utils.semantic_cache import record_source_clearance
except ImportError:
    def record_source_clearance(clearance):
        pass


try:
    from agent_core.tools.registry import register_tool
except ImportError:
    def register_tool(name: str):
        def deco(fn):
            return fn
        return deco

try:
    from agent_core.app_utils.system_config import (
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
        from agent_core.app_utils.sso_auth import require_role
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
                from agent_core.app_utils.sso_auth import is_allow_local_dev_sso, SSOUser, current_sso_user
                if is_allow_local_dev_sso():
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
            from agent_core.app_utils.sso_auth import verify_sso_token, current_sso_user, current_sso_raw_token
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


store: Optional[Any] = None
facts_store: Optional[Any] = None
obligations_store: Optional[Any] = None

_knowledge_store_override: Optional[Any] = None
_facts_store_override: Optional[Any] = None
_obligations_store_override: Optional[Any] = None


def set_knowledge_store(custom_store: Optional[Any]) -> None:
    global _knowledge_store_override
    _knowledge_store_override = custom_store


def reset_knowledge_store() -> None:
    global _knowledge_store_override, store
    _knowledge_store_override = None
    store = None


def set_facts_store(custom_store: Optional[Any]) -> None:
    global _facts_store_override
    _facts_store_override = custom_store


def reset_facts_store() -> None:
    global _facts_store_override, facts_store
    _facts_store_override = None
    facts_store = None


def set_obligations_store(custom_store: Optional[Any]) -> None:
    global _obligations_store_override
    _obligations_store_override = custom_store


def reset_obligations_store() -> None:
    global _obligations_store_override, obligations_store
    _obligations_store_override = None
    obligations_store = None


def _get_active_knowledge_store() -> Any:
    if store is not None:
        return store
    if _knowledge_store_override is not None:
        return _knowledge_store_override
    return get_knowledge_store()


def _get_active_facts_store() -> Any:
    if facts_store is not None:
        return facts_store
    if _facts_store_override is not None:
        return _facts_store_override
    return get_facts_store()


def _get_active_obligations_store() -> Any:
    if obligations_store is not None:
        return obligations_store
    if _obligations_store_override is not None:
        return _obligations_store_override
    return get_obligations_store()


mcp = FastMCP(name="EnterpriseKnowledgeRAG")


@register_tool("lookup_fact")
@mcp.tool()
def lookup_fact(key: str) -> dict:
    """
    Point-lookup một fact cứng (L1) theo key duy nhất, KHÔNG dùng vector search.
    - key: Mã định danh fact dạng domain.entity.property (ví dụ: 'erp.po.sla_hours', 'crm.quote.discount_auto_approve_max_pct', 'hrm.timesheet.payroll_lock_day').
    Trả về thông tin chi tiết của fact gồm value, typed_value, unit, source_document, status hoặc thông báo không tìm thấy.
    """
    if not key or not str(key).strip():
        return {
            "status": "error",
            "message": "Key không được để trống.",
        }
    clean_key = str(key).strip()

    try:
        from agent_core.app_utils.sso_auth import get_current_sso_user
    except ImportError:
        try:
            from app_utils.sso_auth import get_current_sso_user
        except ImportError:
            def get_current_sso_user():
                return None

    current_user = get_current_sso_user()
    if current_user:
        sec_ctx = SecurityContext.from_user(
            user_id=getattr(current_user, "email", getattr(current_user, "user_id", "anonymous")),
            roles=getattr(current_user, "roles", []),
            clearance_level=getattr(current_user, "clearance_level", None),
        )
    else:
        sec_ctx = SecurityContext.anonymous()

    try:
        fact = _get_active_facts_store().get_fact(clean_key)
        if not fact:
            return {
                "status": "not_found",
                "key": clean_key,
                "message": f"Fact '{clean_key}' không tồn tại trong cơ sở tri thức L1 Facts Registry.",
            }

        if not authorize_document(fact, sec_ctx, resource_type="FACT"):
            return {
                "status": "forbidden",
                "error": "Access Denied",
                "key": clean_key,
                "message": f"Fact '{clean_key}' bị từ chối truy cập do không đủ quyền hạn bảo mật.",
            }

        if hasattr(fact, "clearance_level"):
            record_source_clearance(getattr(fact, "clearance_level", 0))

        return {
            "status": "success",
            "fact_id": fact.fact_id,
            "domain": fact.domain,
            "key": fact.key,
            "value": fact.value,
            "typed_value": fact.typed_value(),
            "value_type": fact.value_type,
            "unit": fact.unit,
            "source_document": fact.source_document,
            "date_updated": fact.date_updated,
            "updated_by": fact.updated_by,
            "status_lifecycle": fact.status,
            "notes": fact.notes,
        }
    except KnowledgeStoreUnavailableError as e:
        logger.error("Facts store unavailable: %s", e)
        return {
            "status": "error",
            "key": clean_key,
            "message": "Cơ sở dữ liệu L1 Facts Registry tạm thời gián đoạn. Vui lòng thử lại sau.",
        }
    except Exception as e:
        logger.error("Error during lookup_fact: %s", e)
        return {
            "status": "error",
            "key": clean_key,
            "message": f"Lỗi tra cứu fact: {str(e)}",
        }


@mcp.tool()
def get_obligation(obligation_id: str) -> dict:
    """
    Tra cứu nghĩa vụ pháp lý, điều khoản hợp đồng hoặc cam kết SLA chuẩn mực (L3 Obligations Registry).
    - obligation_id: Mã định danh nghĩa vụ (ví dụ: 'OBL-SAP-001', 'OBL-DPA-001', 'OBL-SEC-001').
    Bảo vệ bởi RBAC & Clearance: kiểm tra tính hợp lệ qua authorize_document.
    """
    try:
        from agent_core.app_utils.sso_auth import get_current_sso_user
    except ImportError:
        try:
            from app_utils.sso_auth import get_current_sso_user
        except ImportError:
            def get_current_sso_user():
                return None

    current_user = get_current_sso_user()
    if current_user:
        sec_ctx = SecurityContext.from_user(
            user_id=getattr(current_user, "email", getattr(current_user, "user_id", "anonymous")),
            roles=getattr(current_user, "roles", []),
            clearance_level=getattr(current_user, "clearance_level", None),
        )
    else:
        sec_ctx = SecurityContext.anonymous()

    if not obligation_id or not str(obligation_id).strip():
        return {
            "status": "error",
            "message": "obligation_id không được để trống.",
        }

    clean_id = str(obligation_id).strip()
    try:
        ob = _get_active_obligations_store().get_obligation(clean_id)
        if not ob:
            return {
                "status": "not_found",
                "obligation_id": clean_id,
                "message": f"Nghĩa vụ pháp lý '{clean_id}' không tồn tại trong cơ sở L3 Obligations Registry.",
            }

        if not authorize_document(ob, sec_ctx, resource_type="OBLIGATION"):
            return {
                "status": "forbidden",
                "error": "Access Denied",
                "obligation_id": clean_id,
                "message": f"Nghĩa vụ pháp lý '{clean_id}' bị từ chối truy cập do không đủ quyền hạn bảo mật.",
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


RETRIEVE_K = 10
FINAL_K = 3


def _filter_by_role(results: list, sec_ctx: SecurityContext) -> list:
    """
    Post-filters retrieved candidates using single source of truth authorize_document.
    """
    if not results:
        return []
    filtered = []
    for r in results:
        if authorize_document(r, sec_ctx, resource_type="KNOWLEDGE_DOCUMENT"):
            filtered.append(r)
    return filtered


@register_tool("search_enterprise_knowledge")
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
        from agent_core.app_utils.sso_auth import get_current_sso_user
    except ImportError:
        try:
            from app_utils.sso_auth import get_current_sso_user
        except ImportError:
            def get_current_sso_user():
                return None

    current_user = get_current_sso_user()
    if current_user:
        sec_ctx = SecurityContext.from_user(
            user_id=getattr(current_user, "email", getattr(current_user, "user_id", "anonymous")),
            roles=getattr(current_user, "roles", []),
            clearance_level=getattr(current_user, "clearance_level", None),
        )
    else:
        sec_ctx = SecurityContext.anonymous()

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
            results = _get_active_knowledge_store().search(
                query=query,
                system=clean_sys,
                limit=RETRIEVE_K,
                security_context=sec_ctx,
            )
            filtered = _filter_by_role(results, sec_ctx)[:FINAL_K]
            for r in filtered:
                if hasattr(r, "clearance_level"):
                    record_source_clearance(getattr(r, "clearance_level", 0))
            return [r.model_dump() for r in filtered]
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
        results = _get_active_knowledge_store().search(
            query=query,
            system="ALL",
            limit=RETRIEVE_K,
            allowed_systems=authorized_systems,
            security_context=sec_ctx,
        )
        filtered = _filter_by_role(results, sec_ctx)[:FINAL_K]
        for r in filtered:
            if hasattr(r, "clearance_level"):
                record_source_clearance(getattr(r, "clearance_level", 0))
        return [r.model_dump() for r in filtered]
    except KnowledgeStoreUnavailableError as e:
        logger.error("Knowledge store unavailable during search ALL: %s", e)
        return [{
            "article_id": "STORE-UNAVAILABLE",
            "title": "Dịch vụ Tra cứu Tri thức Tạm thời Gián đoạn",
            "snippet": "Cơ sở dữ liệu tri thức doanh nghiệp hiện không phản hồi. Vui lòng thử lại sau.",
            "system": "ALL",
            "score": 0.0,
        }]


@register_tool("get_system_manual")
@mcp.tool()
def get_system_manual(article_id: str) -> dict:
    """
    Retrieves the complete technical manual or troubleshooting guide for a specific article ID.
    Enforces domain-level and document-level RBAC for sensitive enterprise system operational guides.
    """
    try:
        from agent_core.app_utils.sso_auth import get_current_sso_user
    except ImportError:
        try:
            from app_utils.sso_auth import get_current_sso_user
        except ImportError:
            def get_current_sso_user():
                return None

    current_user = get_current_sso_user()
    if not current_user:
        return {
            "status": "forbidden",
            "error": "Access Denied",
            "message": "Truy cập bị từ chối: Yêu cầu đăng nhập xác thực SSO để xem tài liệu hệ thống.",
            "article_id": article_id,
        }

    sec_ctx = SecurityContext.from_user(
        user_id=getattr(current_user, "email", getattr(current_user, "user_id", "anonymous")),
        roles=getattr(current_user, "roles", []),
        clearance_level=getattr(current_user, "clearance_level", None),
    )

    try:
        article = _get_active_knowledge_store().get_article_by_id(article_id, security_context=sec_ctx)
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

    if not authorize_document(article, sec_ctx, resource_type="KNOWLEDGE_DOCUMENT"):
        return {
            "status": "forbidden",
            "error": "Access Denied",
            "message": f"Tài liệu '{article_id}' bị từ chối truy cập do không đủ quyền hạn bảo mật.",
            "article_id": article_id,
            "system": getattr(article, "system", "UNKNOWN"),
        }

    if hasattr(article, "clearance_level"):
        record_source_clearance(getattr(article, "clearance_level", 0))

    art_dict = article.model_dump()
    raw_content = art_dict.get("content", "")
    art_dict["content"] = wrap_retrieved_document(
        content=raw_content,
        doc_id=article.id,
        system=article.system,
        title=article.title,
    )
    return {"status": "success", "article": art_dict}


@register_tool("draft_email_response")
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
