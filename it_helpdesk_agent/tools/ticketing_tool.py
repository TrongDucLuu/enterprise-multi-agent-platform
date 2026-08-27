import datetime
import uuid
from typing import Literal, Optional
from pydantic import BaseModel, Field

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

# In-memory ticket storage (simulates Enterprise Service Desk DB / ServiceNow / Jira Service Desk)
_TICKETS_DB: dict[str, HelpdeskTicket] = {}

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
    """
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
    _TICKETS_DB[ticket_id] = ticket
    return {
        "status": "success",
        "message": f"Created ticket {ticket_id}",
        "ticket": ticket.model_dump()
    }

def get_ticket_details(ticket_id: str) -> dict:
    """
    Retrieves full details of a specific Helpdesk ticket.
    """
    ticket = _TICKETS_DB.get(ticket_id.upper())
    if not ticket:
        return {"status": "error", "message": f"Ticket '{ticket_id}' not found."}
    return {"status": "success", "ticket": ticket.model_dump()}

def update_ticket_status(
    ticket_id: str,
    status: TicketStatus,
    resolution_notes: Optional[str] = None,
) -> dict:
    """
    Updates the resolution status and notes for a ticket.
    """
    ticket = _TICKETS_DB.get(ticket_id.upper())
    if not ticket:
        return {"status": "error", "message": f"Ticket '{ticket_id}' not found."}
    
    ticket.status = status
    if resolution_notes:
        ticket.resolution_notes = resolution_notes
    ticket.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
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
    """
    ticket = _TICKETS_DB.get(ticket_id.upper())
    if not ticket:
        return {"status": "error", "message": f"Ticket '{ticket_id}' not found."}
    
    ticket.assigned_tier = target_tier
    ticket.status = "Escalated" if target_tier in ["L2_Enterprise_RAG", "L3_Deep_Diagnostics", "Human_Ops"] else ticket.status
    ticket.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    
    return {
        "status": "success",
        "message": f"Ticket {ticket.id} routed to '{target_tier}'. Reason: {reason}",
        "ticket": ticket.model_dump()
    }

def list_user_tickets(user_id: str) -> dict:
    """
    Lists all tickets associated with a specific user ID.
    """
    user_tickets = [t.model_dump() for t in _TICKETS_DB.values() if t.user_id == user_id]
    return {
        "status": "success",
        "count": len(user_tickets),
        "tickets": user_tickets
    }
