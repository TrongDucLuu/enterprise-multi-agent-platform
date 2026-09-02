import pytest
from unittest.mock import AsyncMock, MagicMock
from agent_core.agent import (
    root_orchestrator,
    created_agents,
    save_session_to_memory_callback,
    fast_model,
    high_reasoning_model,
)

def test_multi_agent_structure_3_tiers():
    assert root_orchestrator.name == "root_triage_orchestrator"
    assert len(root_orchestrator.sub_agents) == 3
    
    sub_agent_names = [agent.name for agent in root_orchestrator.sub_agents]
    assert "l1_selfservice_agent" in sub_agent_names
    assert "l2_enterprise_rag_agent" in sub_agent_names
    assert "l3_deep_diagnostics_agent" in sub_agent_names

def test_models_assigned_appropriately():
    from agent_core.app_utils.env import get_model_names_for_environment
    fast_m, reasoning_m = get_model_names_for_environment()
    l1_selfservice_agent = created_agents["l1_selfservice_agent"]
    l2_enterprise_rag_agent = created_agents["l2_enterprise_rag_agent"]
    l3_deep_diagnostics_agent = created_agents["l3_deep_diagnostics_agent"]
    # L1 and L2 use fast model
    assert l1_selfservice_agent.model.model == fast_m
    assert l2_enterprise_rag_agent.model.model == fast_m
    
    # L3 uses high reasoning model for deep diagnosis & RCA
    assert l3_deep_diagnostics_agent.model.model == reasoning_m

@pytest.mark.asyncio
async def test_save_session_to_memory_callback():
    mock_memory_service = AsyncMock()
    mock_session = MagicMock()
    
    mock_invocation_ctx = MagicMock(memory_service=mock_memory_service, session=mock_session)
    mock_callback_ctx = MagicMock(_invocation_context=mock_invocation_ctx)
    
    await save_session_to_memory_callback(mock_callback_ctx)
    mock_memory_service.add_session_to_memory.assert_awaited_once_with(mock_session)

@pytest.mark.asyncio
async def test_save_session_to_memory_callback_defensive_none():
    await save_session_to_memory_callback(None)
    mock_ctx = MagicMock(_invocation_context=MagicMock(memory_service=None))
    await save_session_to_memory_callback(mock_ctx)
