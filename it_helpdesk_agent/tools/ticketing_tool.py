import datetime
import logging
import os
import uuid
from typing import Literal, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

TicketPriority = Literal["Low", "Medium", "High", "Critical"]
TicketStatus = Literal["Open", "In_Progress", "Escalated", "Resolved", "Closed"]
TicketTier = Literal["L1_SelfService", "L2_Enterprise_RAG", "L3_Deep_Diagnostics", "Human_Ops"]

class HelpdeskTicket(BaseModel):
    id: str
    user_id: str
    title: str
    description: str
    category: str
    priority: TicketPriority
    status: TicketStatus = "Open"
    assigned_tier: TicketTier = "L1_SelfService"
    resolution_notes: Optional[str] = None
    created_at: str
    updated_at: str


# In-memory ticket storage (Local cache & fallback for offline/test environments)
_TICKETS_DB: dict[str, HelpdeskTicket] = {}

# Lazy Firestore Client Initialization
_firestore_client = None
_firestore_initialized = False

def _get_firestore():
    global _firestore_client, _firestore_initialized
    if _firestore_initialized:
        return _firestore_client
    
    use_firestore = os.getenv("USE_FIRESTORE_TICKETS", "false").lower() in ("true", "1") or bool(os.getenv("K_SERVICE"))
    if use_firestore:
        try:
            from google.cloud import firestore
            _firestore_client = firestore.Client()
            logger.info("Connected to Google Cloud Firestore for persistent ticketing storage.")
        except Exception as e:
            logger.warning(f"Firestore unavailable ({e}). Falling back to in-memory ticketing store.")
            _firestore_client = None
    _firestore_initialized = True
    return _firestore_client


def _persist_ticket_to_storage(ticket: HelpdeskTicket):
    """Persists ticket to Firestore if available and updates local cache."""
    _TICKETS_DB[ticket.id] = ticket
    fs = _get_firestore()
    if fs:
        try:
            fs.collection("helpdesk_tickets").document(ticket.id).set(ticket.model_dump())
        except Exception as e:
            logger.error(f"Failed to persist ticket {ticket.id} to Firestore: {e}")


def _load_ticket_from_storage(ticket_id: str) -> Optional[HelpdeskTicket]:
    """Loads ticket from local cache, or fetches from Firestore if missing."""
    ticket_id_norm = ticket_id.upper()
    if ticket_id_norm in _TICKETS_DB:
        return _TICKETS_DB[ticket_id_norm]
    
    fs = _get_firestore()
    if fs:
        try:
            doc = fs.collection("helpdesk_tickets").document(ticket_id_norm).get()
            if doc.exists:
                data = doc.to_dict()
                ticket = HelpdeskTicket(**data)
                _TICKETS_DB[ticket_id_norm] = ticket
                return ticket
        except Exception as e:
            logger.error(f"Error fetching ticket {ticket_id} from Firestore: {e}")
    return None


def _is_privileged_user(user: Optional[object]) -> bool:
    if not user or not hasattr(user, "roles"):
        return False
    user_roles = {r.lower() for r in getattr(user, "roles", [])}
    return bool(user_roles & {"it_admin", "sys_admin", "support_agent", "helpdesk_operator", "compliance_officer", "admin"})


def _check_ticket_access(ticket_user_id: str) -> tuple[bool, Optional[str]]:
    """
    Checks if current authenticated caller is authorized to access ticket belonging to ticket_user_id.
    Returns (is_allowed, error_message).
    """
    try:
        from it_helpdesk_agent.app_utils.sso_auth import get_current_sso_user, ALLOW_LOCAL_DEV_SSO
    except ImportError:
        try:
            from app_utils.sso_auth import get_current_sso_user, ALLOW_LOCAL_DEV_SSO
        except ImportError:
            return False, "Hệ thống xác thực SSO không khả dụng (ImportError). Truy cập bị từ chối theo nguyên tắc Fail-Closed."

    current_user = get_current_sso_user()
    if not current_user:
        if ALLOW_LOCAL_DEV_SSO:
            return True, None
        return False, "Yêu cầu đăng nhập xác thực SSO trước khi truy cập ticket."

    # Admins and Support staff can access any ticket
    if _is_privileged_user(current_user):
        return True, None

    # Normal employee can ONLY access their own ticket (matching user_id or email)
    if current_user.user_id == ticket_user_id or current_user.email.lower() == str(ticket_user_id).lower():
        return True, None

    return False, f"Truy cập bị từ chối: Người dùng '{current_user.email}' không có quyền truy cập ticket của '{ticket_user_id}'."


def create_helpdesk_ticket(
    user_id: str,
    title: str,
    description: str,
    category: str = "General",
    priority: TicketPriority = "Medium",
    initial_tier: TicketTier = "L1_SelfService",
) -> dict:
    """
    Creates a new IT Helpdesk ticket with categorized priority and assigned tier.
    Persists to Firestore when available with local caching.
    """
    # Enforce current user ID if logged in as standard employee to prevent identity spoofing
    try:
        from it_helpdesk_agent.app_utils.sso_auth import get_current_sso_user
        current_user = get_current_sso_user()
        if current_user and not _is_privileged_user(current_user):
            user_id = current_user.user_id
    except Exception:
        pass

    ticket_id = f"TICK-{str(uuid.uuid4())[:8].upper()}"
    now_str = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    ticket = HelpdeskTicket(
        id=ticket_id,
        user_id=user_id,
        title=title,
        description=description,
        category=category,
        priority=priority,
        status="Open",
        assigned_tier=initial_tier,
        created_at=now_str,
        updated_at=now_str
    )
    _persist_ticket_to_storage(ticket)
    return {
        "status": "success",
        "message": f"Created ticket {ticket_id}",
        "ticket": ticket.model_dump()
    }


def get_ticket_details(ticket_id: str) -> dict:
    """
    Retrieves full details of a specific Helpdesk ticket from storage.
    Enforces ownership and RBAC access control.
    """
    ticket = _load_ticket_from_storage(ticket_id)
    if not ticket:
        return {"status": "error", "message": f"Ticket '{ticket_id}' not found."}
    
    allowed, err = _check_ticket_access(ticket.user_id)
    if not allowed:
        return {"status": "error", "message": err}

    return {"status": "success", "ticket": ticket.model_dump()}


def update_ticket_status(
    ticket_id: str,
    status: TicketStatus,
    resolution_notes: Optional[str] = None,
) -> dict:
    """
    Updates the resolution status and notes for a ticket.
    Enforces ownership and role-based access control.
    """
    ticket = _load_ticket_from_storage(ticket_id)
    if not ticket:
        return {"status": "error", "message": f"Ticket '{ticket_id}' not found."}
    
    allowed, err = _check_ticket_access(ticket.user_id)
    if not allowed:
        return {"status": "error", "message": err}

    ticket.status = status
    if resolution_notes:
        ticket.resolution_notes = resolution_notes
    ticket.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _persist_ticket_to_storage(ticket)
    
    return {
        "status": "success",
        "message": f"Ticket {ticket.id} status updated to '{status}'",
        "ticket": ticket.model_dump()
    }


def route_ticket_to_tier(
    ticket_id: str,
    target_tier: TicketTier,
    reason: str,
) -> dict:
    """
    Routes/escalates a ticket to a higher tier or specialist team.
    Enforces ownership and role-based access control.
    """
    ticket = _load_ticket_from_storage(ticket_id)
    if not ticket:
        return {"status": "error", "message": f"Ticket '{ticket_id}' not found."}
    
    allowed, err = _check_ticket_access(ticket.user_id)
    if not allowed:
        return {"status": "error", "message": err}
    
    ticket.assigned_tier = target_tier
    ticket.status = "Escalated" if target_tier in ["L2_Enterprise_RAG", "L3_Deep_Diagnostics", "Human_Ops"] else ticket.status
    ticket.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _persist_ticket_to_storage(ticket)
    
    return {
        "status": "success",
        "message": f"Ticket {ticket.id} routed to '{target_tier}'. Reason: {reason}",
        "ticket": ticket.model_dump()
    }


def list_user_tickets(user_id: str) -> dict:
    """
    Lists all tickets associated with a specific user ID.
    Enforces ownership verification: employees can only list their own tickets.
    """
    allowed, err = _check_ticket_access(user_id)
    if not allowed:
        return {"status": "error", "message": err}

    fs = _get_firestore()
    if fs:
        try:
            docs = fs.collection("helpdesk_tickets").where("user_id", "==", user_id).stream()
            results = []
            for doc in docs:
                t = HelpdeskTicket(**doc.to_dict())
                _TICKETS_DB[t.id] = t
                results.append(t.model_dump())
            if results:
                return {
                    "status": "success",
                    "count": len(results),
                    "tickets": results
                }
        except Exception as e:
            logger.warning(f"Firestore query failed, reading local cache: {e}")

    user_tickets = [t.model_dump() for t in _TICKETS_DB.values() if t.user_id == user_id or (t.user_id and str(t.user_id).lower() == str(user_id).lower())]
    return {
        "status": "success",
        "count": len(user_tickets),
        "tickets": user_tickets
    }

