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
    VALID_STATUS_TRANSITIONS,
)
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture(autouse=True)
def clean_case_store():
    _CASES_DB.clear()
    _CASES_CACHE_TIMES.clear()
    yield
    _CASES_DB.clear()
    _CASES_CACHE_TIMES.clear()


# =========================================================================
# C1: STATUS TRANSITION WHITELIST
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


def test_invalid_status_transition_jumping():
    """Verify illegal jumping of states is rejected (e.g. Open -> Closed or Open -> Resolved directly)."""
    user = SSOUser(user_id="agent-01", email="agent@company.com", roles=["support_agent"])
    token = current_sso_user.set(user)
    try:
        res = create_case(user_id="emp-01", title="Test Illegal Jump", description="Testing illegal jumps")
        case_id = res["case"]["id"]

        # Open -> Closed (Direct jump is forbidden)
        r_fail = update_case_status(case_id=case_id, status="Closed")
        assert r_fail["status"] == "error"
        assert "không hợp lệ" in r_fail["message"]

        # Open -> Resolved (Direct jump without In_Progress is forbidden)
        r_fail2 = update_case_status(case_id=case_id, status="Resolved")
        assert r_fail2["status"] == "error"
        assert "không hợp lệ" in r_fail2["message"]
    finally:
        current_sso_user.reset(token)


def test_terminal_state_cannot_be_updated():
    """Verify closed or cancelled cases cannot transition to any other status."""
    user = SSOUser(user_id="agent-01", email="agent@company.com", roles=["support_agent"])
    token = current_sso_user.set(user)
    try:
        res = create_case(user_id="emp-01", title="Test Closed", description="Testing closed case")
        case_id = res["case"]["id"]

        update_case_status(case_id=case_id, status="In_Progress")
        update_case_status(case_id=case_id, status="Resolved")
        update_case_status(case_id=case_id, status="Closed")

        # Attempt to update Closed case
        r_closed = update_case_status(case_id=case_id, status="In_Progress")
        assert r_closed["status"] == "error"
        assert "trạng thái kết thúc" in r_closed["message"]
    finally:
        current_sso_user.reset(token)


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

        # Employee routing to L2 should be denied
        r_l2 = route_case_to_tier(case_id=case_id, target_tier="L2_Enterprise_RAG", reason="Need L2 help")
        assert r_l2["status"] == "error"
        assert "Không đủ quyền" in r_l2["message"]

        # Employee routing to L3 should be denied
        r_l3 = route_case_to_tier(case_id=case_id, target_tier="L3_Deep_Diagnostics", reason="Need L3 help")
        assert r_l3["status"] == "error"
        assert "Không đủ quyền" in r_l3["message"]
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
        assert r_l3["status"] == "error"
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
