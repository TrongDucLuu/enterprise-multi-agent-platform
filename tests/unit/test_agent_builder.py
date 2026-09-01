"""
Unit tests for agent_builder and domain pack loading.
Verifies version compatibility, tool resolution, prompt injection defense enforcement,
and structural equivalence with hardcoded agent configurations.
"""
import pytest
from agent_core.agent_builder import (
    load_domain_pack,
    build_agent_system,
    assert_core_compatibility,
    INDIRECT_PROMPT_INJECTION_DEFENSE_INSTRUCTION,
)
from agent_core import CORE_VERSION


def test_core_compatibility():
    assert_core_compatibility("2.0.0", "2.0.0")
    assert_core_compatibility("1.9.0", "2.0.0")
    with pytest.raises(RuntimeError):
        assert_core_compatibility("3.0.0", "2.0.0")


def test_load_it_helpdesk_domain_pack():
    pack = load_domain_pack("it-helpdesk")
    assert pack["pack_meta"]["id"] == "it-helpdesk"
    assert pack["pack_meta"]["min_core_version"] == "2.0.0"
    assert "root_triage_orchestrator" in pack["agents_data"]
    assert "l1_selfservice_agent" in pack["agents_data"]
    assert "l2_enterprise_rag_agent" in pack["agents_data"]
    assert "l3_deep_diagnostics_agent" in pack["agents_data"]
    assert "categories" in pack["case_schema"]


def test_build_agent_system_hierarchy():
    root_agent, agents_dict = build_agent_system("it-helpdesk")
    assert root_agent.name == "root_triage_orchestrator"
    assert len(root_agent.sub_agents) == 3

    sub_names = [sa.name for sa in root_agent.sub_agents]
    assert "l1_selfservice_agent" in sub_names
    assert "l2_enterprise_rag_agent" in sub_names
    assert "l3_deep_diagnostics_agent" in sub_names

    # Verify all agents have Indirect Prompt Injection Defense enforced in instructions
    for name, agent in agents_dict.items():
        assert "Indirect Prompt Injection Defense" in agent.instruction
        assert "untrusted reference data" in agent.instruction


def test_tool_resolution_in_agents():
    root_agent, agents_dict = build_agent_system("it-helpdesk")
    l1 = agents_dict["l1_selfservice_agent"]
    l1_tool_names = [getattr(t, "__name__", str(t)) for t in l1.tools]
    assert "create_helpdesk_ticket" in l1_tool_names or "create_case" in l1_tool_names
    assert "lookup_fact" in l1_tool_names

    l3 = agents_dict["l3_deep_diagnostics_agent"]
    l3_tool_names = [getattr(t, "__name__", str(t)) for t in l3.tools]
    assert "analyze_system_logs_for_rca" in l3_tool_names or "analyze_log_rca" in l3_tool_names
    assert "get_obligation" in l3_tool_names
