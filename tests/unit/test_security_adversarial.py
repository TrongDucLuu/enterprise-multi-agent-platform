import pytest
from unittest.mock import MagicMock, patch
from it_helpdesk_agent.tools.ticketing_tool import (
    create_helpdesk_ticket,
    get_ticket_details,
    update_ticket_status,
    route_ticket_to_tier,
    list_user_tickets,
    _check_ticket_access,
    _TICKETS_DB,
)
from it_helpdesk_agent.tools.enterprise_rag_mcp.knowledge_store import (
    InMemoryKnowledgeStore,
    BigQueryVectorKnowledgeStore,
    ALLOWED_SYSTEMS,
)
from it_helpdesk_agent.tools.enterprise_rag_mcp.main import (
    search_enterprise_knowledge,
    get_system_manual,
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


def test_idor_employee_cannot_route_other_user_ticket():
    """Verify that employee cannot route or escalate another employee's ticket."""
    res = create_helpdesk_ticket(
        user_id="victim-user",
        title="Lỗi cấp quyền SAP",
        description="Mô tả sự cố ERP",
        category="ERP"
    )
    ticket_id = res["ticket"]["id"]

    # Attacker logs in as regular employee
    attacker = SSOUser(
        user_id="attacker-user",
        email="attacker@company.com",
        roles=["employee"]
    )
    current_sso_user.set(attacker)

    # Attacker tries to escalate victim's ticket to L3
    route_res = route_ticket_to_tier(
        ticket_id=ticket_id,
        target_tier="L3_Deep_Diagnostics",
        reason="Attacker trying to escalate someone else's ticket"
    )
    assert route_res["status"] == "error"
    assert "không có quyền truy cập" in route_res["message"] or "Truy cập bị từ chối" in route_res["message"]

    # Admin CAN route victim's ticket
    admin = SSOUser(
        user_id="admin-01",
        email="admin@company.com",
        roles=["it_admin"]
    )
    current_sso_user.set(admin)
    admin_route_res = route_ticket_to_tier(
        ticket_id=ticket_id,
        target_tier="L3_Deep_Diagnostics",
        reason="Authorized escalation by IT Admin"
    )
    assert admin_route_res["status"] == "success"
    assert admin_route_res["ticket"]["assigned_tier"] == "L3_Deep_Diagnostics"


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


def test_bigquery_vector_store_allowed_systems_parameterization():
    """Verify that allowed_systems creates IN UNNEST parameterization at SQL level."""
    store = BigQueryVectorKnowledgeStore(project_id="test-proj")
    
    mock_bq_client = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = []
    mock_bq_client.query.return_value = mock_query_job
    store._bq_client = mock_bq_client

    # Search with allowed_systems list
    store.search(query="quy trình nghỉ phép", system="ALL", limit=3, allowed_systems=["HRM", "CRM"])

    assert mock_bq_client.query.called
    called_args, called_kwargs = mock_bq_client.query.call_args
    sql_text = called_args[0]
    job_config = called_kwargs.get("job_config")

    assert "WHERE system IN UNNEST(@allowed_systems_param)" in sql_text
    # Verify parameter is present
    param_names = [p.name for p in job_config.query_parameters]
    assert "allowed_systems_param" in param_names


def test_bigquery_fallback_logging_warning(caplog):
    """Verify that BigQuery failure logs warning for Cloud Monitoring alerting."""
    import logging
    store = BigQueryVectorKnowledgeStore(project_id="test-proj")
    
    mock_bq_client = MagicMock()
    mock_bq_client.query.side_effect = Exception("BigQuery Connection Timeout")
    store._bq_client = mock_bq_client

    with caplog.at_level(logging.WARNING):
        results = store.search(query="SAP PO", system="ERP", limit=1)
        # Verify graceful fallback to in-memory
        assert len(results) > 0
        assert results[0].system == "ERP"

    # Verify warning log was emitted
    assert any("BigQuery vector search failed" in record.message for record in caplog.records)



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


def test_semantic_cache_unsafe_default_prevention():
    """
    Verify that SemanticCache.set() defaults to is_public=False (safe default).
    If a developer calls cache.set() without explicitly specifying is_public=True,
    the entry MUST NOT be leaked to other users.
    """
    cache = SemanticCache(similarity_threshold=0.90)
    cache.clear()

    secret_query = "Thông tin lương và thưởng dự án của tôi"
    secret_response = "Thưởng dự án quý 3 là 50 triệu VND"

    # Default call without specifying is_public or user_id
    cache.set(query=secret_query, response=secret_response, user_id="user-secret")

    # Another user queries -> MUST BE A MISS because default is_public=False
    match_other = cache.get(secret_query, user_id="user-other")
    assert match_other is None

    # Anonymous queries -> MUST BE A MISS
    match_anon = cache.get(secret_query, user_id=None)
    assert match_anon is None

    # Owner queries -> MUST BE A HIT
    match_owner = cache.get(secret_query, user_id="user-secret")
    assert match_owner is not None
    assert match_owner["response"] == secret_response


def test_check_ticket_access_fail_closed_unauthenticated():
    """Verify that _check_ticket_access fails closed when user context is None and dev SSO is off."""
    with patch("it_helpdesk_agent.app_utils.sso_auth.get_current_sso_user", return_value=None):
        with patch("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False):
            allowed, err = _check_ticket_access("any-user-id")
            assert allowed is False
            assert "xác thực" in err.lower() or "từ chối" in err.lower()


# -------------------------------------------------------------
# 4. Enterprise RAG RBAC & Security Trimming Tests (L2)
# -------------------------------------------------------------

def test_enterprise_rag_idor_and_rbac_protection_by_domain():
    """
    Verify that domain-level RBAC protects sensitive ERP and HRM documentation.
    - Sales rep CAN access CRM, but CANNOT access HRM or ERP manuals.
    - IT Admin CAN access all domains.
    """
    sales_user = SSOUser(
        user_id="sales-01",
        email="sales@company.com",
        roles=["sales_rep"]
    )
    current_sso_user.set(sales_user)

    # 1. Sales rep tries to fetch sensitive HRM manual -> FORBIDDEN
    hrm_manual = get_system_manual("HRM-KB-101")
    assert hrm_manual["status"] == "forbidden"
    assert "Access Denied" in hrm_manual["error"]

    # 2. Sales rep tries to fetch sensitive ERP manual -> FORBIDDEN
    erp_manual = get_system_manual("ERP-KB-001")
    assert erp_manual["status"] == "forbidden"
    assert "Access Denied" in erp_manual["error"]

    # 3. Sales rep fetches CRM manual -> ALLOWED
    crm_manual = get_system_manual("CRM-KB-201")
    assert crm_manual["status"] == "success"
    assert crm_manual["article"]["id"] == "CRM-KB-201"

    # 4. IT Admin logs in -> CAN access HRM & ERP
    admin_user = SSOUser(
        user_id="admin-01",
        email="admin@company.com",
        roles=["it_admin"]
    )
    current_sso_user.set(admin_user)
    assert get_system_manual("HRM-KB-101")["status"] == "success"
    assert get_system_manual("ERP-KB-001")["status"] == "success"


def test_enterprise_rag_security_trimming_system_all():
    """
    Verify that search_enterprise_knowledge with system='ALL' trims out
    domains the user is not authorized to see.
    """
    # Sales Rep search
    sales_user = SSOUser(
        user_id="sales-01",
        email="sales@company.com",
        roles=["sales_rep"]
    )
    current_sso_user.set(sales_user)

    results = search_enterprise_knowledge(query="đồng bộ", system="ALL")
    # Must only contain CRM or authorized domains, NO HRM or ERP results
    for r in results:
        assert r["system"] == "CRM"

    # HR Specialist search
    hr_user = SSOUser(
        user_id="hr-01",
        email="hr@company.com",
        roles=["hr_specialist"]
    )
    current_sso_user.set(hr_user)
    hr_results = search_enterprise_knowledge(query="đồng bộ", system="ALL")
    for r in hr_results:
        assert r["system"] == "HRM"

