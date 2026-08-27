import pytest
from it_helpdesk_agent.tools.ticketing_tool import (
    create_helpdesk_ticket,
    get_ticket_details,
    update_ticket_status,
    route_ticket_to_tier,
    list_user_tickets,
    _TICKETS_DB,
)

def setup_function():
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
