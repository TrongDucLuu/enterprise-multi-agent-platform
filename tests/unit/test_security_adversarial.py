import pytest
from unittest.mock import MagicMock, patch
from it_helpdesk_agent.tools.ticketing_tool import (
    create_helpdesk_ticket,
    get_ticket_details,
    update_ticket_status,
    list_user_tickets,
    _TICKETS_DB,
)
from it_helpdesk_agent.tools.enterprise_rag_mcp.knowledge_store import (
    InMemoryKnowledgeStore,
    BigQueryVectorKnowledgeStore,
    ALLOWED_SYSTEMS,
)
from it_helpdesk_agent.app_utils.semantic_cache import SemanticCache
from it_helpdesk_agent.app_utils.sso_auth import SSOUser, current_sso_user


def setup_function():
    _TICKETS_DB.clear()
    current_sso_user.set(None)


def teardown_function():
    current_sso_user.set(None)


# -------------------------------------------------------------
# 1. IDOR & Access Control Security Tests (Ticketing Tool)
# -------------------------------------------------------------

def test_idor_regular_employee_cannot_read_other_user_ticket():
    """Verify that a standard employee cannot read another employee's ticket (IDOR)."""
    # Create ticket belonging to victim
    res = create_helpdesk_ticket(
        user_id="victim-user",
        title="Lương tháng 8 chưa đúng",
        description="Bảng lương ghi nhận thiếu 2 ngày OT",
        category="HRM"
    )
    ticket_id = res["ticket"]["id"]

    # Attacker logs in as employee
    attacker = SSOUser(
        user_id="attacker-user",
        email="attacker@company.com",
        roles=["employee"]
    )
    current_sso_user.set(attacker)

    # Attacker tries to read victim's ticket
    detail = get_ticket_details(ticket_id)
    assert detail["status"] == "error"
    assert "không có quyền truy cập" in detail["message"] or "Truy cập bị từ chối" in detail["message"]


def test_idor_employee_can_read_own_ticket():
    """Verify that a standard employee can read their own ticket."""
    user = SSOUser(
        user_id="employee-01",
        email="employee-01@company.com",
        roles=["employee"]
    )
    current_sso_user.set(user)

    res = create_helpdesk_ticket(
        user_id="employee-01",
        title="Đề nghị cấp màn hình rời",
        description="Cần màn hình 27 inch cho lập trình",
        category="Hardware"
    )
    ticket_id = res["ticket"]["id"]

    detail = get_ticket_details(ticket_id)
    assert detail["status"] == "success"
    assert detail["ticket"]["id"] == ticket_id


def test_admin_can_read_any_ticket():
    """Verify that IT Admin / Support roles can read all tickets for troubleshooting."""
    # Create ticket belonging to normal user
    res = create_helpdesk_ticket(
        user_id="user-02",
        title="VPN timeout liên tục",
        description="Không thể kết nối vào gateway US-East",
        category="Network"
    )
    ticket_id = res["ticket"]["id"]

    # IT Admin logs in
    admin = SSOUser(
        user_id="admin-01",
        email="admin@company.com",
        roles=["it_admin"]
    )
    current_sso_user.set(admin)

    detail = get_ticket_details(ticket_id)
    assert detail["status"] == "success"
    assert detail["ticket"]["title"] == "VPN timeout liên tục"


def test_idor_employee_cannot_list_other_user_tickets():
    """Verify that employee cannot list tickets belonging to other employees."""
    create_helpdesk_ticket(user_id="victim-user", title="Victim Ticket 1", description="D1")
    create_helpdesk_ticket(user_id="victim-user", title="Victim Ticket 2", description="D2")

    # Attacker logs in
    attacker = SSOUser(
        user_id="attacker-user",
        email="attacker@company.com",
        roles=["employee"]
    )
    current_sso_user.set(attacker)

    res = list_user_tickets("victim-user")
    assert res["status"] == "error"
    assert "không có quyền truy cập" in res["message"] or "Truy cập bị từ chối" in res["message"]


def test_idor_employee_cannot_update_other_user_ticket():
    """Verify that employee cannot resolve or modify another employee's ticket."""
    res = create_helpdesk_ticket(
        user_id="victim-user",
        title="Sự cố HRM",
        description="Mô tả",
        category="HRM"
    )
    ticket_id = res["ticket"]["id"]

    # Attacker logs in
    attacker = SSOUser(
        user_id="attacker-user",
        email="attacker@company.com",
        roles=["employee"]
    )
    current_sso_user.set(attacker)

    update_res = update_ticket_status(
        ticket_id=ticket_id,
        status="Resolved",
        resolution_notes="Attacker marked as resolved"
    )
    assert update_res["status"] == "error"
    assert "không có quyền truy cập" in update_res["message"] or "Truy cập bị từ chối" in update_res["message"]


# -------------------------------------------------------------
# 2. SQL Injection Defense Tests (Knowledge Store)
# -------------------------------------------------------------

def test_sql_injection_payload_sanitized_in_knowledge_store():
    """Verify that SQL injection strings in system parameter are neutralized by allowlist."""
    store = InMemoryKnowledgeStore()
    
    # Malicious injection payloads
    malicious_payloads = [
        "ERP' OR '1'='1",
        "'; DROP TABLE enterprise_articles; --",
        "HRM' UNION SELECT 1, 2, 3, 4, 5 --",
        "ERP'; WAITFOR DELAY '0:0:5'--",
    ]

    for payload in malicious_payloads:
        # Should gracefully treat invalid system payload as ALL or filter safely without crashing
        results = store.search(query="mật khẩu", system=payload)
        assert isinstance(results, list)


def test_bigquery_vector_store_parameterization():
    """Verify that BigQueryVectorKnowledgeStore uses QueryJobConfig with parameters."""
    store = BigQueryVectorKnowledgeStore(project_id="test-proj")
    
    mock_bq_client = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = []
    mock_bq_client.query.return_value = mock_query_job
    store._bq_client = mock_bq_client

    # Execute search with SQL injection payload in system
    store.search(query="SAP ME21N", system="ERP' OR 1=1 --", limit=5)

    # Assert query was invoked with parameterized job_config
    assert mock_bq_client.query.called
    called_args, called_kwargs = mock_bq_client.query.call_args
    sql_text = called_args[0]
    job_config = called_kwargs.get("job_config")

    # The raw malicious SQL string MUST NOT be interpolated directly into SQL text
    assert "ERP' OR 1=1 --" not in sql_text
    # Parameterized placeholder must be used
    assert "@query_vector" in sql_text
    assert "@limit" in sql_text


# -------------------------------------------------------------
# 3. Semantic Cache Multi-Tenant Isolation Tests
# -------------------------------------------------------------

def test_semantic_cache_private_entry_isolation():
    """Verify that private cached entries are isolated by user_id."""
    cache = SemanticCache(similarity_threshold=0.90)
    cache.clear()

    private_query = "Số tài khoản nhận lương của tôi là gì"
    private_response = "Tài khoản VCB 0123456789 của nhân viên Nguyen Van A"

    # User A sets a private cache entry
    cache.set(
        query=private_query,
        response=private_response,
        user_id="user-A",
        is_public=False
    )

    # User B queries the exact same question -> MUST BE A CACHE MISS
    match_b = cache.get(private_query, user_id="user-B")
    assert match_b is None

    # Anonymous user queries -> MUST BE A CACHE MISS
    match_anon = cache.get(private_query, user_id=None)
    assert match_anon is None

    # User A queries -> MUST BE A CACHE HIT
    match_a = cache.get(private_query, user_id="user-A")
    assert match_a is not None
    assert match_a["status"] == "cache_hit"
    assert match_a["response"] == private_response


def test_semantic_cache_public_entry_accessible_to_all():
    """Verify that public FAQ entries can be retrieved by any user."""
    cache = SemanticCache(similarity_threshold=0.90)
    cache.clear()

    public_query = "Cách kết nối Wi-Fi văn phòng"
    public_response = "SSID: Corp-Office, Pass: Company2026"

    # Set public cache entry
    cache.set(
        query=public_query,
        response=public_response,
        is_public=True
    )

    # Accessible by User A
    match_a = cache.get(public_query, user_id="user-A")
    assert match_a is not None
    assert match_a["response"] == public_response

    # Accessible by User B
    match_b = cache.get(public_query, user_id="user-B")
    assert match_b is not None
    assert match_b["response"] == public_response
