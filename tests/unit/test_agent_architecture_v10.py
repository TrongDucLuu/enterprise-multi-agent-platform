import os
import tempfile
import pytest
from agent_core.agent import (
    root_orchestrator,
    l1_selfservice_agent,
    l2_enterprise_rag_agent,
    l3_deep_diagnostics_agent,
)
from agent_core.tools.log_analyzer import analyze_system_logs_for_rca
from agent_core.tools.compliance_tool import review_it_contract_sla
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user
from google.adk.tools import preload_memory_tool, load_memory_tool
from agent_core.tools.ticketing_tool import list_user_tickets, get_ticket_details, create_helpdesk_ticket


def test_agent_descriptions_populated():
    """Verify all 4 agents have meaningful non-empty descriptions (P0.2)."""
    agents = [root_orchestrator, l1_selfservice_agent, l2_enterprise_rag_agent, l3_deep_diagnostics_agent]
    for agent in agents:
        assert agent.description is not None, f"Agent {agent.name} missing description"
        assert len(agent.description.strip()) > 20, f"Agent {agent.name} description too short"


def test_disallow_transfer_to_peers_configured():
    """Verify sub-agents have disallow_transfer_to_peers=True (P0.2)."""
    assert l1_selfservice_agent.disallow_transfer_to_peers is True
    assert l2_enterprise_rag_agent.disallow_transfer_to_peers is True
    assert l3_deep_diagnostics_agent.disallow_transfer_to_peers is True


def test_root_orchestrator_tools_and_memory_clean():
    """Verify Root orchestrator has no ticketing tools or LoadMemoryTool (P0.1, P2.6, P2.7)."""
    tool_names = []
    for tool in root_orchestrator.tools:
        if hasattr(tool, "name"):
            tool_names.append(tool.name)
        elif hasattr(tool, "__name__"):
            tool_names.append(tool.__name__)
        elif isinstance(tool, preload_memory_tool.PreloadMemoryTool):
            tool_names.append("PreloadMemoryTool")
        elif isinstance(tool, load_memory_tool.LoadMemoryTool):
            tool_names.append("LoadMemoryTool")

    assert "list_user_tickets" not in tool_names
    assert "get_ticket_details" not in tool_names
    assert "LoadMemoryTool" not in tool_names
    assert "PreloadMemoryTool" in tool_names or any(isinstance(t, preload_memory_tool.PreloadMemoryTool) for t in root_orchestrator.tools)


def test_no_agent_has_load_memory_tool():
    """Verify LoadMemoryTool is not in any agent toolset (P2.6)."""
    all_agents = [root_orchestrator, l1_selfservice_agent, l2_enterprise_rag_agent, l3_deep_diagnostics_agent]
    for agent in all_agents:
        for tool in agent.tools:
            assert not isinstance(tool, load_memory_tool.LoadMemoryTool), f"LoadMemoryTool found in {agent.name}"


@pytest.fixture(autouse=True)
def default_admin_user():
    """Sets an authorized IT admin user in context for tools requiring elevated privileges."""
    user = SSOUser(
        user_id="it-admin-01",
        email="admin@company.com",
        roles=["employee", "it_admin", "sys_admin", "compliance_officer", "legal_counsel"],
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)


def test_large_log_file_reference_rca(monkeypatch):
    """Verify analyze_system_logs_for_rca handles 50KB+ log file via GCS log_ref (P1.4)."""
    from unittest.mock import patch
    monkeypatch.setenv("ALLOWED_ARTIFACT_BUCKET", "company-artifacts-dev")
    lines = ["[2026-09-01 10:00:00] INFO Normal healthcheck ping ok\n"] * 1000
    lines.append("[2026-09-01 10:05:00] ERROR java.lang.OutOfMemoryError: Java heap space at com.erp.OrderService.process(OrderService.java:120)\n")
    fake_log_content = "".join(lines)
    assert len(fake_log_content) > 50 * 1024  # > 50KB

    with patch("agent_core.app_utils.artifact_storage.read_gcs_artifact", return_value=fake_log_content):
        result = analyze_system_logs_for_rca(
            log_ref="gs://company-artifacts-dev/logs/erp.log",
            system_name="ERP System",
            incident_description="Service crashed unexpectedly"
        )
        assert result["status"] == "success"
        assert "OUT_OF_MEMORY" in result["detected_anomalies"]
        assert any("Heap memory" in h or "OOM" in h for h in result["root_cause_hypotheses"])
        assert result["requires_human_review"] is True


def test_large_contract_file_reference_review(monkeypatch):
    """Verify review_it_contract_sla handles contract file via GCS contract_ref (P1.4)."""
    from unittest.mock import patch
    monkeypatch.setenv("ALLOWED_ARTIFACT_BUCKET", "company-artifacts-dev")
    fake_contract = """
MASTER IT SERVICES AGREEMENT
1. Uptime: The Vendor commits to 99.0% service availability per calendar month.
2. Incident Response MTTR: P1 incidents shall be resolved within 48 hours.
3. Service Credits: Maximum penalty cap is 2% of monthly fee.
4. Data Privacy: Vendor may transfer EU customer data to third-party subcontractors without prior consent.
"""
    with patch("agent_core.app_utils.artifact_storage.read_gcs_artifact", return_value=fake_contract):
        result = review_it_contract_sla(
            contract_ref="gs://company-artifacts-dev/contracts/master_agreement.txt",
            vendor_name="CloudCorp",
            focus_area="ALL"
        )
        assert result["status"] == "success"
        assert result["confidence_level"] in ["MEDIUM", "HIGH"]
        assert result["requires_human_review"] is True
        assert len(result["identified_legal_risks"]) > 0


def test_subagent_zero_trust_rbac_enforcement():
    """Verify subagent L1 enforces Zero-Trust SSO RBAC across ticket operations (P0.1)."""
    # Create ticket for user-1
    user1 = SSOUser(user_id="user-1", email="user1@company.com", roles=["employee"])
    token1 = current_sso_user.set(user1)
    try:
        ticket = create_helpdesk_ticket(user_id="user-1", title="WiFi Issue", description="Cannot connect", category="Network")
        ticket_id = ticket["ticket"]["id"]
    finally:
        current_sso_user.reset(token1)

    # User 2 attempts to query User 1's ticket via L1's get_ticket_details tool
    user2 = SSOUser(user_id="user-2", email="user2@company.com", roles=["employee"])
    token2 = current_sso_user.set(user2)
    try:
        details = get_ticket_details(ticket_id)
        assert details["status"] == "error"
        assert "Access Denied" in details["message"] or "Truy cập bị từ chối" in details["message"]
    finally:
        current_sso_user.reset(token2)
