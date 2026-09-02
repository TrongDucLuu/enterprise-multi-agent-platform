import pytest
from agent_core.tools.ticketing_tool import (
    create_helpdesk_ticket,
    get_ticket_details,
    update_ticket_status,
    route_ticket_to_tier,
    list_user_tickets,
    _TICKETS_DB,
)
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture(autouse=True)
def default_ticket_admin():
    """Sets an authorized IT support/admin user in context for general ticket tool tests."""
    _TICKETS_DB.clear()
    user = SSOUser(
        user_id="it-admin-01",
        email="admin@company.com",
        roles=["employee", "it_admin", "support_agent"],
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)
    _TICKETS_DB.clear()

def test_create_and_get_ticket():
    res = create_helpdesk_ticket(
        user_id="emp-01",
        title="Quên mật khẩu Okta",
        description="Không thể đăng nhập vào cổng thông tin",
        category="Identity",
        priority="High",
        initial_tier="L1_SelfService"
    )
    assert res["status"] == "success"
    ticket_id = res["ticket"]["id"]
    assert ticket_id.startswith("TICK-")
    assert res["ticket"]["priority"] == "High"
    assert res["ticket"]["status"] == "Open"

    details = get_ticket_details(ticket_id)
    assert details["status"] == "success"
    assert details["ticket"]["title"] == "Quên mật khẩu Okta"

def test_update_ticket_status():
    res = create_helpdesk_ticket(
        user_id="emp-02",
        title="Lỗi cấp quyền SAP",
        description="Không có quyền tạo PO ME21N",
        category="ERP"
    )
    ticket_id = res["ticket"]["id"]

    step1 = update_ticket_status(ticket_id=ticket_id, status="In_Progress")
    assert step1["status"] == "success"

    update_res = update_ticket_status(
        ticket_id=ticket_id,
        status="Resolved",
        resolution_notes="Đã gán role Z_PROC_PURCHASER"
    )
    assert update_res["status"] == "success"
    assert update_res["ticket"]["status"] == "Resolved"
    assert update_res["ticket"]["resolution_notes"] == "Đã gán role Z_PROC_PURCHASER"

def test_route_ticket_to_tier():
    res = create_helpdesk_ticket(
        user_id="emp-03",
        title="Sập Database Server",
        description="Deadlock trên DB cluster",
        category="Core System"
    )
    ticket_id = res["ticket"]["id"]

    route_res = route_ticket_to_tier(
        ticket_id=ticket_id,
        target_tier="L3_Deep_Diagnostics",
        reason="Sự cố nghiêm trọng cần Root Cause Analysis"
    )
    assert route_res["status"] == "success"
    assert route_res["ticket"]["assigned_tier"] == "L3_Deep_Diagnostics"
    assert route_res["ticket"]["status"] == "Escalated"

def test_list_user_tickets():
    create_helpdesk_ticket(user_id="user-A", title="Ticket 1", description="Desc 1")
    create_helpdesk_ticket(user_id="user-A", title="Ticket 2", description="Desc 2")
    create_helpdesk_ticket(user_id="user-B", title="Ticket 3", description="Desc 3")

    user_a_tickets = list_user_tickets("user-A")
    assert user_a_tickets["status"] == "success"
    assert user_a_tickets["count"] == 2
    assert len(user_a_tickets["tickets"]) == 2


def test_ticket_cache_ttl_and_fallback():
    """Verify ticket cache expires after TTL and falls back to cached version if Firestore fails."""
    import time
    from unittest.mock import MagicMock, patch
    from agent_core.tools import ticketing_tool

    # Create ticket
    res = create_helpdesk_ticket(user_id="emp-ttl", title="TTL Test", description="Testing TTL")
    ticket_id = res["ticket"]["id"]

    # Mock Firestore client
    mock_fs = MagicMock()
    mock_doc = MagicMock()
    mock_doc.exists = True
    mock_doc.to_dict.return_value = {
        "id": ticket_id,
        "user_id": "emp-ttl",
        "title": "TTL Test Updated in Firestore",
        "description": "Testing TTL",
        "category": "General",
        "priority": "Medium",
        "status": "In_Progress",
        "assigned_tier": "L1_SelfService",
        "created_at": res["ticket"]["created_at"],
        "updated_at": res["ticket"]["updated_at"],
    }
    mock_fs.collection.return_value.document.return_value.get.return_value = mock_doc

    with patch("agent_core.tools.ticketing_tool._get_firestore", return_value=mock_fs):
        # 1. Immediate read within TTL should hit cache (title="TTL Test")
        details = get_ticket_details(ticket_id)
        assert details["ticket"]["title"] == "TTL Test"
        assert not mock_fs.collection.called

        # 2. Simulate expired cache (set cache timestamp in the past)
        norm_id = ticket_id.upper()
        ticketing_tool._TICKETS_CACHE_TIMES[norm_id] = time.time() - 100

        # Now read should query Firestore and return updated title
        details_updated = get_ticket_details(ticket_id)
        assert details_updated["ticket"]["title"] == "TTL Test Updated in Firestore"
        assert mock_fs.collection.called

        # 3. Simulate Firestore failure when cache expired -> should fallback to stale cached ticket
        mock_fs.collection.side_effect = Exception("Firestore unavailable")
        ticketing_tool._TICKETS_CACHE_TIMES[norm_id] = time.time() - 100
        fallback_details = get_ticket_details(ticket_id)
        assert fallback_details["status"] == "success"
        assert fallback_details["ticket"]["id"] == ticket_id

