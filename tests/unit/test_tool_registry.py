"""
Unit tests for central Tool Registry, canonical tool names, and alias resolution.
"""
import pytest
from agent_core.tools.registry import (
    TOOL_REGISTRY,
    list_registered_tools,
    get_registered_tool,
    resolve_tools,
)
# Ensure all tool modules are imported so registry is populated
import agent_core.tools.case_tool
import agent_core.tools.log_analyzer
import agent_core.tools.compliance_tool
import agent_core.tools.enterprise_rag_mcp.main


def test_tool_registry_canonical_names_and_uniqueness():
    """
    Asserts that TOOL_REGISTRY only contains domain-neutral canonical tool names
    and that all registered callables are strictly unique (no duplicate registrations).
    """
    expected_canonical_tools = {
        "create_case",
        "get_case",
        "update_case_status",
        "route_case_to_tier",
        "list_user_cases",
        "analyze_log_rca",
        "review_contract_sla",
        "get_obligation",
        "list_contract_obligations",
        "lookup_fact",
        "search_enterprise_knowledge",
        "get_system_manual",
        "draft_email_response",
    }
    registered_names = set(TOOL_REGISTRY.keys())
    assert expected_canonical_tools.issubset(registered_names), (
        f"Missing expected canonical tools: {expected_canonical_tools - registered_names}"
    )

    # Assert legacy alias names are NOT registered as keys in TOOL_REGISTRY
    forbidden_alias_keys = {
        "create_helpdesk_ticket",
        "get_ticket_details",
        "update_ticket_status",
        "route_ticket_to_tier",
        "list_user_tickets",
        "analyze_system_logs_for_rca",
        "review_it_contract_sla",
        "mcp_get_obligation",
    }
    found_forbidden = registered_names.intersection(forbidden_alias_keys)
    assert not found_forbidden, f"Legacy aliases must NOT be registered in TOOL_REGISTRY: {found_forbidden}"

    # Assert all values in TOOL_REGISTRY are unique callables
    callables = list(TOOL_REGISTRY.values())
    unique_callables = set(callables)
    assert len(callables) == len(unique_callables), (
        f"Duplicate callables found in TOOL_REGISTRY. Total: {len(callables)}, Unique: {len(unique_callables)}"
    )


def test_resolve_tools_with_explicit_aliases():
    """Asserts that resolve_tools resolves tool aliases using an explicit mapping."""
    aliases = {
        "create_helpdesk_ticket": "create_case",
        "get_ticket_details": "get_case",
        "analyze_system_logs_for_rca": "analyze_log_rca",
    }
    resolved = resolve_tools(
        ["create_helpdesk_ticket", "get_ticket_details", "analyze_system_logs_for_rca"],
        tool_aliases=aliases,
    )
    assert len(resolved) == 3
    assert resolved[0] == TOOL_REGISTRY["create_case"]
    assert resolved[1] == TOOL_REGISTRY["get_case"]
    assert resolved[2] == TOOL_REGISTRY["analyze_log_rca"]


def test_resolve_tools_with_pack_aliases(pinned_it_helpdesk_pack):
    """Asserts that resolve_tools dynamically resolves it-helpdesk pack aliases."""
    resolved = resolve_tools([
        "create_helpdesk_ticket",
        "review_it_contract_sla",
        "lookup_fact",
    ])
    assert len(resolved) == 3
    assert resolved[0] == TOOL_REGISTRY["create_case"]
    assert resolved[1] == TOOL_REGISTRY["review_contract_sla"]
    assert resolved[2] == TOOL_REGISTRY["lookup_fact"]


def test_resolve_tools_fail_closed_on_unknown():
    """Asserts that resolve_tools raises ValueError when encountering unknown tools."""
    with pytest.raises(ValueError, match="Domain pack references unknown tools: \\['unknown_secret_tool'\\]"):
        resolve_tools(["create_case", "unknown_secret_tool"])


def test_resolve_tools_builtin_preload_memory():
    """Asserts that builtin:preload_memory is resolved successfully."""
    resolved = resolve_tools(["builtin:preload_memory", "lookup_fact"])
    assert len(resolved) == 2
    assert resolved[0].__class__.__name__ == "PreloadMemoryTool"
    assert resolved[1] == TOOL_REGISTRY["lookup_fact"]
