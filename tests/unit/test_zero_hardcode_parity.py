"""
Zero-hardcode architectural AST check, ADK discovery smoke test, and Case Schema enforcement tests.
"""
import ast
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from agent_core.agent import app as adk_app, root_agent
from agent_core.fast_api_app import app as api_app
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user
from agent_core.tools.case_tool import (
    create_case,
    update_case_status,
    route_case_to_tier,
    get_case_schema,
    _CASES_DB,
)


@pytest.fixture
def auth_sso_user():
    """Sets an authorized test user in context for case operations."""
    _CASES_DB.clear()
    user = SSOUser(
        user_id="test_user@company.com",
        email="test_user@company.com",
        roles=["employee", "it_admin", "support_agent"],
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)
    _CASES_DB.clear()


def test_agent_py_has_zero_hardcoded_agent_instances():
    """
    AST verification: Ensures agent_core/agent.py does NOT instantiate any Agent(...) directly.
    All agents must be dynamically built by build_agent_system().
    """
    agent_py_path = Path(__file__).resolve().parent.parent.parent / "agent_core" / "agent.py"
    assert agent_py_path.is_file(), f"agent.py not found at {agent_py_path}"

    tree = ast.parse(agent_py_path.read_text(encoding="utf-8"), filename=str(agent_py_path))

    agent_call_count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "Agent":
                agent_call_count += 1
            elif isinstance(func, ast.Attribute) and func.attr == "Agent":
                agent_call_count += 1

    assert agent_call_count == 0, (
        f"Found {agent_call_count} hardcoded Agent(...) calls in agent_core/agent.py! "
        f"All agents must be instantiated via build_agent_system()."
    )


def test_adk_app_discovery_smoke():
    """
    Verifies that root_agent and app are properly configured for ADK discovery.
    """
    assert root_agent is not None
    assert root_agent.name == "root_triage_orchestrator"
    assert len(root_agent.sub_agents) == 3

    assert adk_app is not None
    assert adk_app.root_agent == root_agent


def test_fast_api_app_discovery_and_health():
    """
    Smoke test for Starlette / FastAPI app mounted with ADK routes and Lifespan wrapper.
    """
    with TestClient(api_app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ("healthy", "degraded", "ok")


def test_case_schema_enforcement_and_validation(auth_sso_user):
    """
    Verifies that case_schema.yaml rules are strictly enforced by case_tool.
    """
    schema = get_case_schema()
    assert "statuses" in schema
    assert "Open" in schema["statuses"]
    assert "Resolved" in schema["statuses"]

    # 1. Create a case
    res = create_case(
        user_id="test_user@company.com",
        title="Cannot connect to VPN",
        description="VPN client fails with timeout",
        category="Network",
        priority="High"
    )
    assert res["status"] == "success"
    case_id = res["case"]["id"]

    # 2. Update with valid status
    valid_res = update_case_status(case_id=case_id, status="In_Progress", resolution_notes="Investigating VPN logs")
    assert valid_res["status"] == "success"
    assert valid_res["case"]["status"] == "In_Progress"

    # 3. Update with invalid status (should be rejected fail-closed)
    invalid_res = update_case_status(case_id=case_id, status="INVALID_CUSTOM_STATUS")
    assert invalid_res["status"] == "error"
    assert "không hợp lệ theo case schema" in invalid_res["message"]

    # 4. Route case to valid tier
    route_res = route_case_to_tier(case_id=case_id, target_tier="L2_Enterprise_RAG", reason="Escalating to network specialist")
    assert route_res["status"] == "success"
    assert route_res["case"]["assigned_tier"] == "L2_Enterprise_RAG"


def test_no_module_level_pydantic_knowledge_or_obligations_instantiation():
    """
    AST check: Scans agent_core/ to ensure no module-level statements instantiate
    domain-specific Pydantic models (KnowledgeArticle, Fact, Obligation).
    All sample data must be lazy-loaded from active domain pack YAMLs.
    """
    agent_core_dir = Path(__file__).resolve().parent.parent.parent / "agent_core"
    forbidden_classes = {"KnowledgeArticle", "Fact", "Obligation"}

    violations = []
    for py_file in agent_core_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for stmt in tree.body:
            # Check top-level assignments
            if isinstance(stmt, (ast.Assign, ast.AnnAssign)):
                for node in ast.walk(stmt):
                    if isinstance(node, ast.Call):
                        func_name = None
                        if isinstance(node.func, ast.Name):
                            func_name = node.func.id
                        elif isinstance(node.func, ast.Attribute):
                            func_name = node.func.attr
                        if func_name in forbidden_classes:
                            violations.append(f"{py_file.name}:{stmt.lineno} instantiates {func_name} at module level")

    assert not violations, (
        f"Found forbidden top-level domain model instantiations in agent_core:\n"
        + "\n".join(violations)
    )

