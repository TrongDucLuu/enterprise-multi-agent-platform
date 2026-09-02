"""
Generic Case Management Tool module for agent_core.
Provides domain-neutral case creation, retrieval, updates, escalation, and listing.
Enforces status transition whitelist, tier routing RBAC, Firestore fail-closed in production,
optimistic concurrency control (OCC), and append-only audit trail.
"""
import datetime
import logging
import os
import time
import uuid
from collections import OrderedDict
import threading
from typing import Optional
from pydantic import BaseModel, Field

from agent_core.tools.registry import register_tool

logger = logging.getLogger(__name__)

CASE_COLLECTION = os.getenv("CASE_COLLECTION", "cases")
MAX_LOCAL_CASES_CACHE = int(os.getenv("MAX_LOCAL_CASES_CACHE", os.getenv("MAX_LOCAL_TICKETS_CACHE", "1000")))
CASE_CACHE_TTL_SECONDS = int(os.getenv("CASE_CACHE_TTL_SECONDS", os.getenv("TICKET_CACHE_TTL_SECONDS", "30")))

VALID_STATUS_TRANSITIONS: dict[str, list[str]] = {
    "Open": ["In_Progress", "Cancelled"],
    "In_Progress": ["Resolved", "Escalated", "Cancelled"],
    "Escalated": ["In_Progress", "Resolved", "Cancelled"],
    "Resolved": ["Closed", "In_Progress"],
    "Closed": [],
    "Cancelled": [],
}

TIER_REQUIRED_ROLES: dict[str, set[str]] = {
    "L1_SelfService": {"employee", "support_agent", "it_admin", "sys_admin", "security_auditor", "admin", "lead"},
    "L1": {"employee", "support_agent", "it_admin", "sys_admin", "security_auditor", "admin", "lead"},
    "L2_Enterprise_RAG": {"support_agent", "it_admin", "sys_admin", "security_auditor", "admin", "lead"},
    "L2": {"support_agent", "it_admin", "sys_admin", "security_auditor", "admin", "lead"},
    "L3_Deep_Diagnostics": {"it_admin", "sys_admin", "security_auditor", "admin", "lead"},
    "L3": {"it_admin", "sys_admin", "security_auditor", "admin", "lead"},
    "Human_Ops": {"support_agent", "it_admin", "sys_admin", "security_auditor", "admin", "lead"},
}


def _is_prod() -> bool:
    try:
        from agent_core.app_utils.env import is_production_mode
        return is_production_mode()
    except Exception:
        return os.getenv("ENVIRONMENT", "").lower() == "production" or bool(os.getenv("K_SERVICE"))


_CASE_SCHEMA_CACHE = None


def get_case_schema() -> dict:
    """Loads and caches case schema configuration from active domain pack."""
    global _CASE_SCHEMA_CACHE
    if _CASE_SCHEMA_CACHE is not None:
        return _CASE_SCHEMA_CACHE

    try:
        from agent_core.agent_builder import load_domain_pack
        pack = load_domain_pack()
        schema = pack.get("case_schema", {})
        if schema:
            _CASE_SCHEMA_CACHE = schema
            return schema
    except Exception as e:
        logger.debug("Failed to load case schema from domain pack: %s", e)

    _CASE_SCHEMA_CACHE = {
        "categories": ["General", "Hardware", "Software", "Access_Permissions", "Network", "ERP", "HRM", "CRM", "Other"],
        "priorities": ["Low", "Medium", "High", "Critical"],
        "tiers": ["L1_SelfService", "L2_Enterprise_RAG", "L3_Deep_Diagnostics", "Human_Ops"],
        "statuses": ["Open", "In_Progress", "Escalated", "Resolved", "Closed", "Cancelled"],
        "initial_tier": "L1_SelfService",
        "default_category": "General",
        "default_priority": "Medium",
    }
    return _CASE_SCHEMA_CACHE


class CaseRecord(BaseModel):
    id: str
    user_id: str
    title: str
    description: str
    category: str = "General"
    priority: str = "Medium"
    status: str = "Open"
    assigned_tier: str = "L1_SelfService"
    resolution_notes: Optional[str] = None
    created_at: str
    updated_at: str
    version: int = 1
    history: list[dict] = Field(default_factory=list)
    routed_by: Optional[str] = None
    routed_at: Optional[str] = None


# Backwards compatibility alias
HelpdeskTicket = CaseRecord

# In-memory LRU case storage (Local cache & fallback for offline/test environments)
_CASES_DB: OrderedDict[str, CaseRecord] = OrderedDict()
_CASES_CACHE_TIMES: dict[str, float] = {}
_cases_cache_lock = threading.Lock()


def _cache_put_case(case: CaseRecord) -> None:
    """Inserts or updates case in local LRU cache with eviction when size exceeds limit."""
    with _cases_cache_lock:
        norm_id = case.id.upper()
        if norm_id in _CASES_DB:
            _CASES_DB.move_to_end(norm_id)
        _CASES_DB[norm_id] = case
        _CASES_CACHE_TIMES[norm_id] = time.time()
        while len(_CASES_DB) > MAX_LOCAL_CASES_CACHE:
            oldest_id, _ = _CASES_DB.popitem(last=False)
            _CASES_CACHE_TIMES.pop(oldest_id, None)


def _cache_get_case(case_id: str, max_age: Optional[float] = None) -> Optional[CaseRecord]:
    """Retrieves case from LRU cache, checking expiration against TTL."""
    with _cases_cache_lock:
        norm_id = case_id.upper()
        if norm_id in _CASES_DB:
            _CASES_DB.move_to_end(norm_id)
            if max_age is not None:
                cached_time = _CASES_CACHE_TIMES.get(norm_id, 0.0)
                if (time.time() - cached_time) > max_age:
                    return None  # Expired
            return _CASES_DB[norm_id]
        return None


def _cache_get_fallback(case_id: str) -> Optional[CaseRecord]:
    """Retrieves case from LRU cache regardless of TTL for offline/error fallback."""
    with _cases_cache_lock:
        norm_id = case_id.upper()
        return _CASES_DB.get(norm_id)


# Lazy Firestore Client Initialization
_firestore_client = None
_firestore_initialized = False


def _get_firestore():
    import sys
    tt = sys.modules.get("agent_core.tools.ticketing_tool")
    if tt and hasattr(tt, "_get_firestore") and tt._get_firestore is not _get_firestore:
        try:
            return tt._get_firestore()
        except Exception:
            pass

    global _firestore_client, _firestore_initialized
    if _firestore_initialized:
        return _firestore_client

    use_firestore = os.getenv("USE_FIRESTORE_TICKETS", os.getenv("USE_FIRESTORE_CASES", "false")).lower() in ("true", "1") or bool(os.getenv("K_SERVICE"))
    is_prod = _is_prod()

    if is_prod or use_firestore:
        try:
            from google.cloud import firestore
            _firestore_client = firestore.Client()
            logger.info("Connected to Google Cloud Firestore for persistent case storage.")
        except Exception as e:
            if is_prod:
                raise RuntimeError(f"Firestore connection failed in production mode: {e}") from e
            logger.warning(f"Firestore initialization failed ({e}). Running in DEV mode with in-memory case store. Data will NOT persist.")
            _firestore_client = None
    else:
        logger.warning("Running in DEV mode with in-memory case store. Data will NOT persist.")
        _firestore_client = None

    _firestore_initialized = True
    return _firestore_client


def _persist_case_to_storage(case: CaseRecord):
    """Persists case to Firestore if available and updates local LRU cache. Enforces fail-closed in prod."""
    _cache_put_case(case)
    is_prod = _is_prod()
    fs = _get_firestore()
    if fs:
        try:
            fs.collection(CASE_COLLECTION).document(case.id).set(case.model_dump())
        except Exception as e:
            if is_prod:
                raise RuntimeError(f"Firestore write operation failed in production mode: {e}") from e
            logger.error(f"Failed to persist case {case.id} to Firestore: {e}")


def _load_case_from_storage(case_id: str) -> Optional[CaseRecord]:
    """Loads case from local LRU cache if unexpired, or fetches fresh from Firestore with fail-closed behavior."""
    case_id_norm = case_id.upper()
    is_prod = _is_prod()
    fs = _get_firestore()

    # If Firestore is not enabled (e.g. offline dev, test mode), use local cache directly
    if fs is None:
        if is_prod:
            raise RuntimeError("Firestore case store unavailable in production mode.")
        return _cache_get_fallback(case_id_norm)

    # If Firestore is enabled, check unexpired cache first (TTL-based)
    cached = _cache_get_case(case_id_norm, max_age=CASE_CACHE_TTL_SECONDS)
    if cached is not None:
        return cached

    # Cache miss or expired: fetch fresh from Firestore
    try:
        doc = fs.collection(CASE_COLLECTION).document(case_id_norm).get()
        if doc.exists:
            data = doc.to_dict()
            case = CaseRecord(**data)
            _cache_put_case(case)
            return case
    except Exception as e:
        if is_prod:
            raise RuntimeError(f"Firestore read operation failed in production mode: {e}") from e
        logger.error(f"Error fetching case {case_id} from Firestore: {e}. Using stale cache fallback in DEV mode.")
        return _cache_get_fallback(case_id_norm)

    return None


def _is_privileged_user(user: Optional[object]) -> bool:
    if not user or not hasattr(user, "roles"):
        return False
    user_roles = {r.lower() for r in getattr(user, "roles", [])}
    return bool(user_roles & {"it_admin", "sys_admin", "support_agent", "helpdesk_operator", "compliance_officer", "admin", "case_manager", "lead", "security_auditor"})


def _check_case_access(case_user_id: str) -> tuple[bool, Optional[str]]:
    """
    Checks if current authenticated caller is authorized to access case belonging to case_user_id.
    Returns (is_allowed, error_message).
    """
    try:
        from agent_core.app_utils.sso_auth import get_current_sso_user, is_allow_local_dev_sso
    except ImportError:
        try:
            from app_utils.sso_auth import get_current_sso_user, is_allow_local_dev_sso
        except ImportError:
            return False, "Hệ thống xác thực SSO không khả dụng (ImportError). Truy cập bị từ chối theo nguyên tắc Fail-Closed."

    current_user = get_current_sso_user()
    if not current_user:
        if is_allow_local_dev_sso():
            return True, None
        return False, "Yêu cầu đăng nhập xác thực SSO trước khi truy cập case/ticket."

    # Admins and Support staff can access any case
    if _is_privileged_user(current_user):
        return True, None

    # Normal user can ONLY access their own case (matching user_id or email)
    if current_user.user_id == case_user_id or current_user.email.lower() == str(case_user_id).lower():
        return True, None

    return False, f"Truy cập bị từ chối: Người dùng '{current_user.email}' không có quyền truy cập case của '{case_user_id}'."


def _load_and_authorize_case(case_id: str) -> tuple[Optional[CaseRecord], Optional[dict]]:
    """
    Helper function to load a case and verify caller authorization.
    Returns (case, None) if authorized, or (None, error_response_dict) if not found or denied.
    """
    case = _load_case_from_storage(case_id)
    if not case:
        return None, {"status": "error", "message": f"Case/Ticket '{case_id}' not found."}

    allowed, err = _check_case_access(case.user_id)
    if not allowed:
        return None, {"status": "error", "message": err}

    return case, None


@register_tool("create_case")
def create_case(
    user_id: str,
    title: str,
    description: str,
    category: str = "General",
    priority: str = "Medium",
    initial_tier: str = "L1_SelfService",
    id_prefix: str = "CASE",
) -> dict:
    """
    Creates a new case record with specified details, priority, and assigned tier.
    Persists to storage backend with local caching and audit trail history.
    """
    current_user = None
    try:
        from agent_core.app_utils.sso_auth import get_current_sso_user
        current_user = get_current_sso_user()
        if current_user and not _is_privileged_user(current_user):
            user_id = current_user.user_id
    except Exception:
        pass

    case_id = f"{id_prefix}-{str(uuid.uuid4())[:8].upper()}"
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    actor = current_user.email if current_user else user_id

    initial_history = [
        {
            "timestamp": now_str,
            "action": "create",
            "actor": actor,
            "details": {
                "title": title,
                "category": category,
                "priority": priority,
                "assigned_tier": initial_tier,
                "status": "Open",
            },
            "version_after": 1,
        }
    ]

    case = CaseRecord(
        id=case_id,
        user_id=user_id,
        title=title,
        description=description,
        category=category,
        priority=priority,
        status="Open",
        assigned_tier=initial_tier,
        created_at=now_str,
        updated_at=now_str,
        version=1,
        history=initial_history,
    )
    _persist_case_to_storage(case)
    return {
        "status": "success",
        "message": f"Created {id_prefix.lower()} {case_id}",
        "ticket": case.model_dump(),
        "case": case.model_dump(),
    }


@register_tool("create_helpdesk_ticket")
def create_helpdesk_ticket(
    user_id: str,
    title: str,
    description: str,
    category: str = "General",
    priority: str = "Medium",
    initial_tier: str = "L1_SelfService",
) -> dict:
    """Backwards compatibility wrapper for create_case."""
    return create_case(
        user_id=user_id,
        title=title,
        description=description,
        category=category,
        priority=priority,
        initial_tier=initial_tier,
        id_prefix="TICK",
    )


@register_tool("get_case")
def get_case(case_id: str) -> dict:
    """
    Retrieves full details of a specific case record by ID.
    Enforces ownership and role-based access control.
    """
    case, err_resp = _load_and_authorize_case(case_id)
    if err_resp:
        return err_resp

    return {"status": "success", "ticket": case.model_dump(), "case": case.model_dump()}


@register_tool("get_ticket_details")
def get_ticket_details(ticket_id: str) -> dict:
    """Backwards compatibility wrapper for get_case."""
    return get_case(ticket_id)


@register_tool("update_case_status")
def update_case_status(
    case_id: str,
    status: str,
    resolution_notes: Optional[str] = None,
    expected_version: Optional[int] = None,
) -> dict:
    """
    Updates the resolution status and notes for a case.
    Enforces ownership, role-based access control, transition whitelist, and OCC.
    """
    case, err_resp = _load_and_authorize_case(case_id)
    if err_resp:
        return err_resp

    # Optimistic Concurrency Control (OCC)
    if expected_version is not None and case.version != expected_version:
        return {
            "status": "error",
            "message": "Conflict: case has been modified by another transaction. Please reload and retry.",
            "current_version": case.version,
            "expected_version": expected_version,
            "case_id": case.id,
        }

    schema = get_case_schema()
    valid_statuses = schema.get("statuses", [])
    if valid_statuses and status not in valid_statuses:
        return {
            "status": "error",
            "message": f"Trạng thái '{status}' không hợp lệ theo case schema. Các trạng thái hợp lệ: {valid_statuses}",
            "case_id": case.id,
            "ticket_id": case.id,
        }

    # Terminal state check
    if case.status in ("Closed", "Cancelled"):
        return {
            "status": "error",
            "message": f"Không thể cập nhật case đã ở trạng thái kết thúc '{case.status}'.",
            "case_id": case.id,
            "ticket_id": case.id,
        }

    # Whitelist transition check
    allowed_transitions = VALID_STATUS_TRANSITIONS.get(case.status, [])
    if status != case.status and status not in allowed_transitions:
        return {
            "status": "error",
            "message": f"Chuyển trạng thái từ '{case.status}' sang '{status}' không hợp lệ. Các trạng thái được phép: {allowed_transitions}",
            "case_id": case.id,
            "ticket_id": case.id,
        }

    current_user = None
    try:
        from agent_core.app_utils.sso_auth import get_current_sso_user
        current_user = get_current_sso_user()
    except Exception:
        pass
    actor = current_user.email if current_user else case.user_id

    old_status = case.status
    case.status = status
    if resolution_notes:
        case.resolution_notes = resolution_notes
    case.version += 1
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    case.updated_at = now_str

    case.history.append({
        "timestamp": now_str,
        "action": "status_change",
        "actor": actor,
        "details": {
            "from": old_status,
            "to": status,
            "resolution_notes": resolution_notes,
        },
        "version_after": case.version,
    })

    _persist_case_to_storage(case)

    return {
        "status": "success",
        "message": f"Case/Ticket {case.id} status updated to '{status}'",
        "ticket": case.model_dump(),
        "case": case.model_dump(),
    }


@register_tool("update_ticket_status")
def update_ticket_status(
    ticket_id: str,
    status: str,
    resolution_notes: Optional[str] = None,
    expected_version: Optional[int] = None,
) -> dict:
    """Backwards compatibility wrapper for update_case_status."""
    return update_case_status(
        case_id=ticket_id,
        status=status,
        resolution_notes=resolution_notes,
        expected_version=expected_version,
    )


@register_tool("route_case_to_tier")
def route_case_to_tier(
    case_id: str,
    target_tier: str,
    reason: str,
    expected_version: Optional[int] = None,
) -> dict:
    """
    Routes/escalates a case to a higher tier or specialist team.
    Enforces ownership, role-based access control, transition rules, and OCC.
    """
    case, err_resp = _load_and_authorize_case(case_id)
    if err_resp:
        return err_resp

    # Terminal state check
    if case.status in ("Closed", "Cancelled"):
        return {
            "status": "error",
            "message": f"Không thể chuyển tuyến case đã ở trạng thái kết thúc '{case.status}'.",
            "case_id": case.id,
            "ticket_id": case.id,
        }

    # Optimistic Concurrency Control (OCC)
    if expected_version is not None and case.version != expected_version:
        return {
            "status": "error",
            "message": "Conflict: case has been modified by another transaction. Please reload and retry.",
            "current_version": case.version,
            "expected_version": expected_version,
            "case_id": case.id,
        }

    # RBAC Tier Routing Check (C2)
    current_user = None
    try:
        from agent_core.app_utils.sso_auth import get_current_sso_user
        current_user = get_current_sso_user()
    except Exception:
        pass

    if current_user:
        user_roles = {r.lower() for r in getattr(current_user, "roles", [])}
        required_roles = TIER_REQUIRED_ROLES.get(target_tier)
        if required_roles is not None and not (user_roles & required_roles):
            return {
                "status": "error",
                "message": f"Không đủ quyền để chuyển case đến tier '{target_tier}'.",
                "case_id": case.id,
                "ticket_id": case.id,
            }

    soft_warning_msg = None
    if target_tier in ["L3_Deep_Diagnostics", "L3"]:
        from agent_core.app_utils.rate_limiter import check_l3_rate_limit_with_warning
        caller_id = current_user.user_id if current_user else None

        allowed, rem, retry_after, is_soft_warning, warn_msg = check_l3_rate_limit_with_warning(caller_id)
        if not allowed:
            l3_rpm = int(os.getenv("L3_RATE_LIMIT_PER_MINUTE", "10"))
            return {
                "status": "error",
                "error_code": "L3_RATE_LIMIT_EXCEEDED",
                "message": f"Hạn mức leo thang lên L3 phân tích chuyên sâu đã vượt quá giới hạn ({l3_rpm} lượt/phút). Vui lòng thử lại sau {retry_after}s.",
                "ticket_id": case.id,
                "case_id": case.id,
            }
        if is_soft_warning and warn_msg:
            soft_warning_msg = warn_msg

    old_tier = case.assigned_tier
    case.assigned_tier = target_tier
    if target_tier in ["L2_Enterprise_RAG", "L3_Deep_Diagnostics", "Human_Ops", "L2", "L3"]:
        case.status = "Escalated"

    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    actor = current_user.email if current_user else case.user_id
    case.routed_by = actor
    case.routed_at = now_str
    case.version += 1
    case.updated_at = now_str

    case.history.append({
        "timestamp": now_str,
        "action": "route",
        "actor": actor,
        "details": {
            "from": old_tier,
            "to": target_tier,
            "reason": reason,
        },
        "version_after": case.version,
    })

    _persist_case_to_storage(case)

    resp = {
        "status": "success",
        "message": f"Case {case.id} routed to '{target_tier}'. Reason: {reason}",
        "ticket": case.model_dump(),
        "case": case.model_dump(),
    }
    if soft_warning_msg:
        resp["soft_warning"] = soft_warning_msg
        resp["message"] += f"\n{soft_warning_msg}"
    return resp


@register_tool("route_ticket_to_tier")
def route_ticket_to_tier(
    ticket_id: str,
    target_tier: str,
    reason: str,
    expected_version: Optional[int] = None,
) -> dict:
    """Backwards compatibility wrapper for route_case_to_tier."""
    return route_case_to_tier(
        case_id=ticket_id,
        target_tier=target_tier,
        reason=reason,
        expected_version=expected_version,
    )


@register_tool("list_user_cases")
def list_user_cases(user_id: str, limit: int = 50) -> dict:
    """
    Lists cases associated with a specific user ID with bounded pagination limit.
    Enforces ownership verification: users can only list their own cases.
    """
    allowed, err = _check_case_access(user_id)
    if not allowed:
        return {"status": "error", "message": err}

    bounded_limit = max(1, min(int(limit), 100))
    is_prod = _is_prod()

    fs = _get_firestore()
    if fs:
        try:
            from google.cloud.firestore_v1.base_query import FieldFilter
            query = fs.collection(CASE_COLLECTION).where(filter=FieldFilter("user_id", "==", user_id)).limit(bounded_limit)
            docs = query.stream()
            results = []
            for doc in docs:
                t = CaseRecord(**doc.to_dict())
                _cache_put_case(t)
                results.append(t.model_dump())
            if results:
                return {
                    "status": "success",
                    "count": len(results),
                    "tickets": results,
                    "cases": results,
                }
        except Exception as e:
            if is_prod:
                raise RuntimeError(f"Firestore query failed in production mode: {e}") from e
            logger.warning(f"Firestore query failed, reading local cache in DEV mode: {e}")

    with _cases_cache_lock:
        user_cases = [
            t.model_dump()
            for t in _CASES_DB.values()
            if t.user_id == user_id or (t.user_id and str(t.user_id).lower() == str(user_id).lower())
        ][:bounded_limit]

    return {
        "status": "success",
        "count": len(user_cases),
        "tickets": user_cases,
        "cases": user_cases,
    }


@register_tool("list_user_tickets")
def list_user_tickets(user_id: str, limit: int = 50) -> dict:
    """Backwards compatibility wrapper for list_user_cases."""
    return list_user_cases(user_id=user_id, limit=limit)

