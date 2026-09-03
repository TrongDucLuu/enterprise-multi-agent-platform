"""
Parity tests verifying dynamic builder output matches production requirements.
Tests tool resolution (including builtin:preload_memory), dynamic template interpolation,
P1-05 return-to-root injection, prompt injection defense, and callback wiring.
"""
import pytest
from google.adk.tools import preload_memory_tool
from agent_core.agent_builder import build_agent_system, load_domain_pack
from agent_core.runtime import (
    fast_model,
    high_reasoning_model,
    semantic_cache_before_model_callback,
    semantic_cache_after_model_callback,
    save_session_to_memory_callback,
)


def test_builder_it_helpdesk_parity():
    """Verifies that it-helpdesk domain pack builds exact expected hierarchy and properties."""
    root_agent, agents_dict = build_agent_system("it-helpdesk")

    assert root_agent.name == "root_triage_orchestrator"
    assert len(root_agent.sub_agents) == 3

    # 1. Root agent properties
    root_tool_types = [type(t) for t in root_agent.tools]
    assert preload_memory_tool.PreloadMemoryTool in root_tool_types

    assert root_agent.model == fast_model
    assert root_agent.before_model_callback == semantic_cache_before_model_callback
    assert root_agent.after_model_callback == semantic_cache_after_model_callback
    assert root_agent.after_agent_callback == save_session_to_memory_callback

    # 2. Template interpolation assertions
    assert "${systems_list}" not in root_agent.instruction
    assert "ERP" in root_agent.instruction

    l2_agent = agents_dict["l2_enterprise_rag_agent"]
    assert "${systems_prompt}" not in l2_agent.instruction
    assert "SOP" in l2_agent.instruction

    # 3. P1-05 Return to root rule injection
    l1_agent = agents_dict["l1_selfservice_agent"]
    l3_agent = agents_dict["l3_deep_diagnostics_agent"]

    for sub_agent in (l1_agent, l2_agent, l3_agent):
        assert "Quy tắc Chuyển giao Ngược về Điều Phối" in sub_agent.instruction
        assert "root_triage_orchestrator" in sub_agent.instruction

    # 4. Indirect Prompt Injection Defense enforcement
    for name, agent in agents_dict.items():
        assert "Indirect Prompt Injection Defense" in agent.instruction
        assert "untrusted reference data" in agent.instruction

    # 5. Model mapping
    assert l1_agent.model == fast_model
    assert l2_agent.model == fast_model
    assert l3_agent.model == high_reasoning_model

    # 6. Tool count and resolution
    l1_tool_names = [getattr(t, "__name__", str(t)) for t in l1_agent.tools]
    assert "lookup_fact" in l1_tool_names
    assert "search_enterprise_knowledge" in l1_tool_names
    assert "create_case" in l1_tool_names

    l3_tool_names = [getattr(t, "__name__", str(t)) for t in l3_agent.tools]
    assert "analyze_log_rca" in l3_tool_names
    assert "review_contract_sla" in l3_tool_names
    assert "get_obligation" in l3_tool_names


def test_builder_reads_template_agents_yaml():
    """Verifies that _template domain pack builds successfully without code changes."""
    root_agent, agents_dict = build_agent_system("_template")
    assert root_agent.name == "root_orchestrator"
    assert len(root_agent.sub_agents) == 1
    assert agents_dict["specialist_agent"].name == "specialist_agent"
    assert "lookup_fact" in [getattr(t, "__name__", str(t)) for t in agents_dict["specialist_agent"].tools]


@pytest.mark.boot
def test_subprocess_clean_boot_with_template_pack():
    """Verify clean FastAPI boot in subprocess with DOMAIN_PACK=_template in production mode."""
    import os
    import sys
    import subprocess

    env = os.environ.copy()
    env["DOMAIN_PACK"] = "_template"
    env["ENVIRONMENT"] = "production"
    env["ALLOWED_DOMAINS"] = "company.com"

    cmd = [sys.executable, "-c", "from agent_core.fast_api_app import app; print(app.title)"]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"Template pack boot failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert "FastAPI" in res.stdout


@pytest.mark.boot
def test_subprocess_clean_boot_with_it_helpdesk_pack():
    """Verify clean FastAPI boot in subprocess with DOMAIN_PACK=it-helpdesk in production mode."""
    import os
    import sys
    import subprocess

    env = os.environ.copy()
    env["DOMAIN_PACK"] = "it-helpdesk"
    env["ENVIRONMENT"] = "production"
    env["ALLOWED_DOMAINS"] = "company.com"

    cmd = [sys.executable, "-c", "from agent_core.fast_api_app import app; print(app.title)"]
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    assert res.returncode == 0, f"IT-Helpdesk pack boot failed:\nSTDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
    assert "FastAPI" in res.stdout

