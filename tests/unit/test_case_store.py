import pytest
from unittest.mock import MagicMock, patch
from agent_core.tools.case_tool import (
    create_case,
    get_case,
    update_case_status,
    route_case_to_tier,
    list_user_cases,
    _CASES_DB,
    _CASES_CACHE_TIMES,
    get_case_schema,
    clear_case_schema_cache,
)
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture(autouse=True)
def clean_case_store(fake_firestore, pinned_it_helpdesk_pack):
    """Pins it-helpdesk pack because this suite tests the IT Helpdesk case schema (tiers, roles, transitions)."""
    _CASES_DB.clear()
    _CASES_CACHE_TIMES.clear()
    clear_case_schema_cache()
    yield
    _CASES_DB.clear()
    _CASES_CACHE_TIMES.clear()
    clear_case_schema_cache()


# =========================================================================
# C1: STATUS TRANSITION WHITELIST & ROLE DIMENSION (PHẦN F)
# =========================================================================

def test_valid_status_transitions():
    """Verify standard happy-path status transitions: Open -> In_Progress -> Resolved -> Closed."""
    user = SSOUser(user_id="agent-01", email="agent@company.com", roles=["support_agent"])
    token = current_sso_user.set(user)
    try:
        res = create_case(user_id="emp-01", title="Test Transition", description="Testing transitions")
        case_id = res["case"]["id"]
        assert res["case"]["status"] == "Open"

        # Open -> In_Progress
        r1 = update_case_status(case_id=case_id, status="In_Progress")
        assert r1["status"] == "success"
        assert r1["case"]["status"] == "In_Progress"

        # In_Progress -> Resolved
        r2 = update_case_status(case_id=case_id, status="Resolved", resolution_notes="Fixed")
        assert r2["status"] == "success"
        assert r2["case"]["status"] == "Resolved"

        # Resolved -> Closed
        r3 = update_case_status(case_id=case_id, status="Closed")
        assert r3["status"] == "success"
        assert r3["case"]["status"] == "Closed"
    finally:
        current_sso_user.reset(token)


def test_six_required_cases_for_status_transitions_and_role_routing():
    """
    Test the 6 specific cases required by PHẦN F Work Order:
    1. owner đóng ticket của mình (theo schema) -> success
    2. owner set Resolved -> forbidden
    3. owner leo thang L3 -> forbidden
    4. privileged Open -> Resolved -> success (chứng minh chiều vai trò hoạt động)
    5. privileged leo thang L3 -> success
    6. chuyển tiếp không có trong schema -> error
    """
    owner_user = SSOUser(user_id="emp-01", email="emp01@company.com", roles=["employee"])
    priv_user = SSOUser(user_id="admin-01", email="admin01@company.com", roles=["it_admin"])

    # Case 1: Owner closes their own ticket (Open -> Closed is allowed for owner in schema)
    token = current_sso_user.set(owner_user)
    try:
        res1 = create_case(user_id="emp-01", title="Case 1 Owner Close", description="Testing owner close")
        c1_id = res1["case"]["id"]
        r1 = update_case_status(case_id=c1_id, status="Closed")
        assert r1["status"] == "success"
        assert r1["case"]["status"] == "Closed"
    finally:
        current_sso_user.reset(token)

    # Case 2: Owner tries to set Resolved (forbidden for owner)
    token = current_sso_user.set(owner_user)
    try:
        res2 = create_case(user_id="emp-01", title="Case 2 Owner Resolve", description="Testing owner resolve")
        c2_id = res2["case"]["id"]
        r2 = update_case_status(case_id=c2_id, status="Resolved")
        assert r2["status"] == "forbidden"
        assert "yêu cầu quyền đặc quyền" in r2["message"]
    finally:
        current_sso_user.reset(token)

    # Case 3: Owner tries to escalate L3 (forbidden for owner)
    token = current_sso_user.set(owner_user)
    try:
        res3 = create_case(user_id="emp-01", title="Case 3 Owner L3", description="Testing owner L3 escalate")
        c3_id = res3["case"]["id"]
        r3 = route_case_to_tier(case_id=c3_id, target_tier="L3_Deep_Diagnostics", reason="Want L3 help")
        assert r3["status"] == "forbidden"
    finally:
        current_sso_user.reset(token)

    # Case 4: Privileged Open -> Resolved (success — proves role dimension works!)
    token = current_sso_user.set(priv_user)
    try:
        res4 = create_case(user_id="emp-01", title="Case 4 Priv Resolve", description="Testing direct resolve by admin")
        c4_id = res4["case"]["id"]
        r4 = update_case_status(case_id=c4_id, status="Resolved", resolution_notes="Instant fix by admin")
        assert r4["status"] == "success"
        assert r4["case"]["status"] == "Resolved"
    finally:
        current_sso_user.reset(token)

    # Case 5: Privileged escalates L3 (success)
    token = current_sso_user.set(priv_user)
    try:
        res5 = create_case(user_id="emp-01", title="Case 5 Priv L3", description="Testing L3 routing by admin")
        c5_id = res5["case"]["id"]
        r5 = route_case_to_tier(case_id=c5_id, target_tier="L3_Deep_Diagnostics", reason="Deep diagnosis needed")
        assert r5["status"] == "success"
        assert r5["case"]["assigned_tier"] == "L3_Deep_Diagnostics"
    finally:
        current_sso_user.reset(token)

    # Case 6: Transition not in schema -> error
    token = current_sso_user.set(priv_user)
    try:
        res6 = create_case(user_id="emp-01", title="Case 6 Invalid Trans", description="Testing invalid transition")
        c6_id = res6["case"]["id"]
        # Closed -> In_Progress is not allowed in schema (Closed can only go to Open)
        update_case_status(case_id=c6_id, status="Closed")
        r6 = update_case_status(case_id=c6_id, status="In_Progress")
        assert r6["status"] == "error"
        assert "không hợp lệ theo case schema" in r6["message"]
    finally:
        current_sso_user.reset(token)


def test_schema_missing_transitions_fail_closed_in_production(monkeypatch):
    """Verify that in production mode, missing status_transitions in case_schema raises RuntimeError."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    clear_case_schema_cache()

    with patch("agent_core.agent_builder.load_domain_pack", return_value={"case_schema": {"statuses": ["Open"]}}):
        with pytest.raises(RuntimeError, match="Missing required 'status_transitions'"):
            get_case_schema()


# =========================================================================
# C2: ROLE-BASED ACCESS CONTROL (RBAC) FOR ROUTING
# =========================================================================

def test_tier_routing_rbac_employee_denied():
    """Verify a regular employee cannot route a case to L2 or L3."""
    user = SSOUser(user_id="emp-01", email="emp@company.com", roles=["employee"])
    token = current_sso_user.set(user)
    try:
        res = create_case(user_id="emp-01", title="Test RBAC", description="Need help")
        case_id = res["case"]["id"]

        # Employee routing to L2 should be denied with forbidden
        r_l2 = route_case_to_tier(case_id=case_id, target_tier="L2_Enterprise_RAG", reason="Need L2 help")
        assert r_l2["status"] == "forbidden"

        # Employee routing to L3 should be denied with forbidden
        r_l3 = route_case_to_tier(case_id=case_id, target_tier="L3_Deep_Diagnostics", reason="Need L3 help")
        assert r_l3["status"] == "forbidden"
    finally:
        current_sso_user.reset(token)


def test_tier_routing_rbac_support_agent_allowed_l2_denied_l3():
    """Verify support_agent can route to L2, but requires it_admin to route to L3."""
    user = SSOUser(user_id="agent-01", email="agent@company.com", roles=["employee", "support_agent"])
    token = current_sso_user.set(user)
    try:
        res = create_case(user_id="emp-01", title="Test Agent RBAC", description="Need escalation")
        case_id = res["case"]["id"]

        # Support agent can route to L2
        r_l2 = route_case_to_tier(case_id=case_id, target_tier="L2_Enterprise_RAG", reason="Complex search needed")
        assert r_l2["status"] == "success"
        assert r_l2["case"]["assigned_tier"] == "L2_Enterprise_RAG"
        assert r_l2["case"]["routed_by"] == "agent@company.com"
        assert r_l2["case"]["routed_at"] is not None

        # Support agent cannot route to L3
        r_l3 = route_case_to_tier(case_id=case_id, target_tier="L3_Deep_Diagnostics", reason="Root cause needed")
        assert r_l3["status"] == "forbidden"
        assert "Không đủ quyền" in r_l3["message"]
    finally:
        current_sso_user.reset(token)


def test_tier_routing_rbac_it_admin_allowed_l3():
    """Verify it_admin can route case to L3."""
    user = SSOUser(user_id="admin-01", email="admin@company.com", roles=["employee", "it_admin"])
    token = current_sso_user.set(user)
    try:
        res = create_case(user_id="emp-01", title="Test Admin L3", description="Server outage")
        case_id = res["case"]["id"]

        r_l3 = route_case_to_tier(case_id=case_id, target_tier="L3_Deep_Diagnostics", reason="Kernel panic investigation")
        assert r_l3["status"] == "success"
        assert r_l3["case"]["assigned_tier"] == "L3_Deep_Diagnostics"
        assert r_l3["case"]["routed_by"] == "admin@company.com"
    finally:
        current_sso_user.reset(token)


# =========================================================================
# D1: FIRESTORE FAIL-CLOSED IN PRODUCTION
# =========================================================================

def test_firestore_fail_closed_in_production():
    """Verify that in production mode, Firestore errors immediately raise RuntimeError instead of silent in-memory fallback."""
    user = SSOUser(user_id="admin-01", email="admin@company.com", roles=["it_admin"])
    token = current_sso_user.set(user)
    try:
        # Simulate production environment
        with patch("agent_core.tools.case_tool._is_prod", return_value=True):
            # 1. Firestore init failure in production -> raises RuntimeError
            with patch("agent_core.tools.case_tool._firestore_initialized", False):
                mock_firestore_mod = MagicMock()
                mock_firestore_mod.Client.side_effect = Exception("Connection refused")
                with patch.dict("sys.modules", {"google.cloud.firestore": mock_firestore_mod}):
                    with pytest.raises(RuntimeError, match="Firestore connection failed in production mode"):
                        create_case(user_id="emp-prod", title="Prod Fail", description="Should fail")

            # 2. Firestore read failure in production -> raises RuntimeError (No stale fallback!)
            mock_fs = MagicMock()
            mock_fs.collection.side_effect = Exception("Firestore read timeout")
            with patch("agent_core.tools.case_tool._get_firestore", return_value=mock_fs):
                with pytest.raises(RuntimeError, match="Firestore read operation failed in production mode"):
                    get_case("CASE-12345678")
    finally:
        current_sso_user.reset(token)


# =========================================================================
# D2: OPTIMISTIC CONCURRENCY CONTROL (OCC)
# =========================================================================

def test_optimistic_concurrency_control():
    """Verify version increments on update and conflict is returned if expected_version does not match."""
    user = SSOUser(user_id="admin-01", email="admin@company.com", roles=["it_admin"])
    token = current_sso_user.set(user)
    try:
        res = create_case(user_id="emp-occ", title="OCC Test", description="Testing OCC")
        case_id = res["case"]["id"]
        assert res["case"]["version"] == 1

        # Successful update with matching version
        u1 = update_case_status(case_id=case_id, status="In_Progress", expected_version=1)
        assert u1["status"] == "success"
        assert u1["case"]["version"] == 2

        # Stale update with old expected_version=1 must be rejected
        u_conflict = update_case_status(case_id=case_id, status="Resolved", expected_version=1)
        assert u_conflict["status"] == "error"
        assert "Conflict" in u_conflict["message"]
        assert u_conflict["current_version"] == 2

        # Route with matching expected_version=2
        r1 = route_case_to_tier(case_id=case_id, target_tier="L2_Enterprise_RAG", reason="Escalating", expected_version=2)
        assert r1["status"] == "success"
        assert r1["case"]["version"] == 3
    finally:
        current_sso_user.reset(token)


# =========================================================================
# D3: AUDIT TRAIL / HISTORY
# =========================================================================

def test_audit_trail_history():
    """Verify case maintains an append-only audit trail with actor, timestamp, action, and version details."""
    user = SSOUser(user_id="admin-01", email="admin@company.com", roles=["it_admin"])
    token = current_sso_user.set(user)
    try:
        res = create_case(user_id="emp-audit", title="Audit Test", description="Testing history")
        case_id = res["case"]["id"]

        update_case_status(case_id=case_id, status="In_Progress", resolution_notes="Work started")
        route_case_to_tier(case_id=case_id, target_tier="L2_Enterprise_RAG", reason="Escalated to L2")
        update_case_status(case_id=case_id, status="Resolved", resolution_notes="Completed")

        details = get_case(case_id)
        history = details["case"]["history"]
        assert len(history) == 4

        # 1. create
        assert history[0]["action"] == "create"
        assert history[0]["actor"] == "admin@company.com"
        assert history[0]["version_after"] == 1

        # 2. status_change -> In_Progress
        assert history[1]["action"] == "status_change"
        assert history[1]["details"]["from"] == "Open"
        assert history[1]["details"]["to"] == "In_Progress"
        assert history[1]["version_after"] == 2

        # 3. route -> L2_Enterprise_RAG
        assert history[2]["action"] == "route"
        assert history[2]["details"]["to"] == "L2_Enterprise_RAG"
        assert history[2]["version_after"] == 3

        # 4. status_change -> Resolved
        assert history[3]["action"] == "status_change"
        assert history[3]["details"]["to"] == "Resolved"
        assert history[3]["version_after"] == 4
    finally:
        current_sso_user.reset(token)


# =========================================================================
# E: FIRESTORE DEPENDENCY INJECTION & FAIL-CLOSED TESTS
# =========================================================================

def test_firestore_fail_closed_in_production_when_missing_client(monkeypatch):
    """
    Verifies that in production mode, if no Firestore client is injected
    and Google Cloud Firestore credentials are not available, _get_firestore() fails closed with RuntimeError.
    """
    from agent_core.tools.case_tool import (
        reset_firestore_client,
        _get_firestore,
    )
    reset_firestore_client()
    monkeypatch.setenv("ENVIRONMENT", "production")
    mock_fs_mod = MagicMock()
    mock_fs_mod.Client.side_effect = Exception("No GCP ADC credentials")
    with patch.dict("sys.modules", {"google.cloud.firestore": mock_fs_mod}):
        with pytest.raises(RuntimeError) as exc_info:
            _get_firestore()
        assert "Firestore connection failed in production mode" in str(exc_info.value)


def test_firestore_dependency_injection_with_factory():
    """
    Verifies that set_firestore_factory allows custom lazy initialization.
    """
    from agent_core.tools.case_tool import (
        set_firestore_factory,
        reset_firestore_client,
        _get_firestore,
    )
    reset_firestore_client()
    mock_client = MagicMock()
    set_firestore_factory(lambda: mock_client)
    try:
        client = _get_firestore()
        assert client is mock_client
    finally:
        reset_firestore_client()

