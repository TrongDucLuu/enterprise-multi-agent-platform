import datetime
from typing import Any, Optional, Union
from agent_core.knowledge.base import SecurityContext
from agent_core.app_utils.audit import emit_authorization_event

def get_field(doc: Any, key: str, default: Any = None) -> Any:
    if isinstance(doc, dict):
        return doc.get(key, default)
    return getattr(doc, key, default)

def resolve_doc_clearance(doc: Any, resource_type: str = "KNOWLEDGE_DOCUMENT") -> int:
    explicit = get_field(doc, "clearance_level", None)
    if explicit is not None:
        try:
            return int(explicit)
        except (ValueError, TypeError):
            pass
            
    sens = str(get_field(doc, "sensitivity", "") or "").upper().strip()
    if sens == "PUBLIC":
        return 0
    elif sens == "CONFIDENTIAL":
        return 2
    elif sens == "RESTRICTED":
        return 3
    elif sens == "INTERNAL":
        return 1

    # Check obligation severity if applicable
    if resource_type == "OBLIGATION" or get_field(doc, "obligation_id", None) is not None:
        return 2

    return 1

def authorize_document(
    doc: Any,
    security_context: SecurityContext,
    resource_type: str = "KNOWLEDGE_DOCUMENT",
    action: str = "READ",
    session_id: Optional[str] = None,
    emit_audit: bool = True,
) -> bool:
    """
    Unified authorization check for any document/fact/record against a SecurityContext.
    Enforces 5 security dimensions:
    1. Tombstone check (is_deleted IS NOT TRUE)
    2. Date validity (effective_date <= today <= expiry_date)
    3. Clearance level (doc.clearance_level <= context.clearance_level)
    4. Allowed roles RBAC (doc.allowed_roles empty or user has matching role/admin)
    5. Sensitivity / Public classification
    
    Fail-closed: returns False and emits DENY audit event on any authorization failure.
    """
    doc_id = str(get_field(doc, "id", None) or get_field(doc, "article_id", None) or get_field(doc, "fact_id", None) or get_field(doc, "obligation_id", "unknown"))
    
    # 1. Tombstone check
    is_deleted = bool(get_field(doc, "is_deleted", False))
    if is_deleted:
        if emit_audit:
            emit_authorization_event(
                actor_id=security_context.user_id,
                actor_roles=security_context.roles,
                actor_clearance=security_context.clearance_level,
                resource_type=resource_type,
                resource_id=doc_id,
                action=action,
                decision="DENY",
                reason_code="TOMBSTONED",
                session_id=session_id,
            )
        return False

    # 2. Date validity check
    today_str = datetime.date.today().isoformat()
    eff_date = get_field(doc, "effective_date", None)
    if eff_date and str(eff_date).strip():
        if str(eff_date).strip() > today_str:
            if emit_audit:
                emit_authorization_event(
                    actor_id=security_context.user_id,
                    actor_roles=security_context.roles,
                    actor_clearance=security_context.clearance_level,
                    resource_type=resource_type,
                    resource_id=doc_id,
                    action=action,
                    decision="DENY",
                    reason_code="NOT_YET_EFFECTIVE",
                    session_id=session_id,
                )
            return False

    exp_date = get_field(doc, "expiry_date", None)
    if exp_date and str(exp_date).strip():
        if str(exp_date).strip() < today_str:
            if emit_audit:
                emit_authorization_event(
                    actor_id=security_context.user_id,
                    actor_roles=security_context.roles,
                    actor_clearance=security_context.clearance_level,
                    resource_type=resource_type,
                    resource_id=doc_id,
                    action=action,
                    decision="DENY",
                    reason_code="EXPIRED",
                    session_id=session_id,
                )
            return False

    # 3. Clearance level check
    doc_clearance = resolve_doc_clearance(doc, resource_type=resource_type)
    if doc_clearance > security_context.clearance_level:
        if emit_audit:
            emit_authorization_event(
                actor_id=security_context.user_id,
                actor_roles=security_context.roles,
                actor_clearance=security_context.clearance_level,
                resource_type=resource_type,
                resource_id=doc_id,
                action=action,
                decision="DENY",
                reason_code="CLEARANCE_INSUFFICIENT",
                session_id=session_id,
            )
        return False

    # 4. Allowed roles RBAC check
    raw_doc_roles = get_field(doc, "allowed_roles", [])
    doc_roles = [str(r).lower().strip() for r in (raw_doc_roles or []) if r]
    if not doc_roles and (resource_type == "OBLIGATION" or get_field(doc, "obligation_id", None) is not None):
        try:
            from agent_core.app_utils.system_config import get_obligation_default_roles
            doc_roles = get_obligation_default_roles()
        except Exception:
            doc_roles = ["compliance_officer", "legal_counsel", "it_admin", "sys_admin"]

    user_roles = [str(r).lower().strip() for r in security_context.roles if r]
    
    try:
        from agent_core.app_utils.system_config import get_admin_roles
        admin_roles = get_admin_roles()
    except Exception:
        admin_roles = ["admin", "super_admin", "it_admin", "sys_admin"]

    is_admin = any(r in admin_roles or r in ("admin", "super_admin") for r in user_roles)
    
    if doc_roles:
        if not is_admin and not any(r in user_roles for r in doc_roles):
            if emit_audit:
                emit_authorization_event(
                    actor_id=security_context.user_id,
                    actor_roles=security_context.roles,
                    actor_clearance=security_context.clearance_level,
                    resource_type=resource_type,
                    resource_id=doc_id,
                    action=action,
                    decision="DENY",
                    reason_code="ROLE_MISMATCH",
                    session_id=session_id,
                )
            return False
    else:
        # Unclassified / Empty doc_roles: If doc is not PUBLIC (clearance >= 1) and user is unauthenticated (clearance == 0)
        if doc_clearance > 0 and security_context.clearance_level == 0:
            if emit_audit:
                emit_authorization_event(
                    actor_id=security_context.user_id,
                    actor_roles=security_context.roles,
                    actor_clearance=security_context.clearance_level,
                    resource_type=resource_type,
                    resource_id=doc_id,
                    action=action,
                    decision="DENY",
                    reason_code="CLEARANCE_INSUFFICIENT",
                    session_id=session_id,
                )
            return False

    # All checks passed
    if emit_audit:
        emit_authorization_event(
            actor_id=security_context.user_id,
            actor_roles=security_context.roles,
            actor_clearance=security_context.clearance_level,
            resource_type=resource_type,
            resource_id=doc_id,
            action=action,
            decision="ALLOW",
            reason_code="SUCCESS",
            session_id=session_id,
        )
    return True
