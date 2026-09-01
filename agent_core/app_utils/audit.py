import json
import logging
import time
from typing import Optional, Any

audit_logger = logging.getLogger("enterprise_agent_audit")

def emit_authorization_event(
    actor_id: str,
    actor_roles: list[str],
    actor_clearance: int,
    resource_type: str,
    resource_id: str,
    action: str,
    decision: str,  # "ALLOW" | "DENY"
    reason_code: str,
    session_id: Optional[str] = None,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Emits a structured authorization audit event for security tracking and compliance.
    
    CRITICAL: Never include raw document contents or sensitive customer payloads
    in audit events.
    """
    # Defensive truncation of resource_id / identifiers to prevent log stuffing
    safe_resource_id = str(resource_id)[:200]
    safe_actor_id = str(actor_id)[:100]
    
    event = {
        "event_type": "AUTHZ_DECISION",
        "timestamp": time.time(),
        "actor_id": safe_actor_id,
        "actor_roles": [str(r)[:50] for r in actor_roles],
        "actor_clearance": int(actor_clearance),
        "resource_type": str(resource_type).upper()[:50],
        "resource_id": safe_resource_id,
        "action": str(action).upper()[:30],
        "decision": "ALLOW" if decision.upper() == "ALLOW" else "DENY",
        "reason_code": str(reason_code).upper()[:50],
        "session_id": str(session_id)[:100] if session_id else None,
    }
    
    if extra_metadata:
        # Sanitize extra metadata: ensure all values are short
        safe_meta = {}
        for k, v in extra_metadata.items():
            str_v = str(v)
            safe_meta[str(k)[:50]] = str_v[:200]
        event["metadata"] = safe_meta
        
    audit_logger.info(json.dumps(event))
    return event
