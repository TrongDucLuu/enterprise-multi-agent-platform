"""
Backwards compatibility shim for ticketing_tool module, delegating to case_tool.
"""
from agent_core.tools.case_tool import (
    CASE_COLLECTION,
    MAX_LOCAL_CASES_CACHE as MAX_LOCAL_TICKETS_CACHE,
    CASE_CACHE_TTL_SECONDS as TICKET_CACHE_TTL_SECONDS,
    CaseRecord,
    HelpdeskTicket,
    _cache_put_case as _cache_put_ticket,
    _cache_get_case as _cache_get_ticket,
    _cache_get_fallback,
    _CASES_DB as _TICKETS_DB,
    _CASES_CACHE_TIMES as _TICKETS_CACHE_TIMES,
    _cases_cache_lock as _tickets_cache_lock,
    _get_firestore,
    _persist_case_to_storage as _persist_ticket_to_storage,
    _load_case_from_storage as _load_ticket_from_storage,
    _is_privileged_user,
    _check_case_access as _check_ticket_access,
    _load_and_authorize_case as _load_and_authorize_ticket,
    create_case,
    create_helpdesk_ticket,
    get_case,
    get_ticket_details,
    update_case_status,
    update_ticket_status,
    route_case_to_tier,
    route_ticket_to_tier,
    list_user_cases,
    list_user_tickets,
)

# Literal type aliases for backwards compatibility with tests
from typing import Literal
TicketPriority = Literal["Low", "Medium", "High", "Critical"]
TicketStatus = Literal["Open", "In_Progress", "Escalated", "Resolved", "Closed"]
TicketTier = Literal["L1_SelfService", "L2_Enterprise_RAG", "L3_Deep_Diagnostics", "Human_Ops"]
