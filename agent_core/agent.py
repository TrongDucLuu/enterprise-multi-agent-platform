"""
Main agent system entrypoint for agent_core.
Constructs the multi-agent hierarchy dynamically via build_agent_system() from domain packs.
Exposes root_orchestrator, root_agent, and app for Google GenAI ADK server execution.
"""
import logging
from google.adk.apps import App
from agent_core.agent_builder import build_agent_system
from agent_core.app_utils.sso_auth import current_sso_user
from agent_core.app_utils.semantic_cache import get_semantic_cache
from agent_core.runtime import (
    fast_model,
    high_reasoning_model,
    save_session_to_memory_callback,
    semantic_cache_before_model_callback,
    semantic_cache_after_model_callback,
    _is_safe_public_faq,
    _current_l3_soft_warning,
    _turn_start_time,
)

logger = logging.getLogger("agent_core")

# Dynamically construct the agent system from active domain pack
root_orchestrator, created_agents = build_agent_system()
root_agent = root_orchestrator

# Export individual sub-agents for backwards compatibility
l1_selfservice_agent = created_agents.get("l1_selfservice_agent")
l2_enterprise_rag_agent = created_agents.get("l2_enterprise_rag_agent")
l3_deep_diagnostics_agent = created_agents.get("l3_deep_diagnostics_agent")

# ADK Application Root
app = App(root_agent=root_orchestrator, name="agent_core")
