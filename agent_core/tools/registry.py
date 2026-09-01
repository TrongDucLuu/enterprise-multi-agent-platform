"""
Tool Registry module for agent_core.
Provides central dynamic registration and resolution of core tools and domain plugins.
"""
import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)

TOOL_REGISTRY: dict[str, Callable] = {}


def register_tool(name: str):
    """Decorator to register a function in the central TOOL_REGISTRY."""
    def deco(fn: Callable) -> Callable:
        if name in TOOL_REGISTRY and TOOL_REGISTRY[name] is not fn:
            logger.warning("Overwriting existing tool registration for %s", name)
        TOOL_REGISTRY[name] = fn
        return fn
    return deco


def get_registered_tool(name: str) -> Optional[Callable]:
    """Retrieves a registered tool by its unique name."""
    return TOOL_REGISTRY.get(name)


def list_registered_tools() -> list[str]:
    """Returns a list of all registered tool names."""
    return sorted(list(TOOL_REGISTRY.keys()))


def resolve_tools(names: list[str]) -> list[Callable]:
    """
    Resolves a list of tool names to their registered callable functions.
    Raises ValueError if any tool is unknown (fail-closed).
    """
    missing = [n for n in names if n not in TOOL_REGISTRY]
    if missing:
        raise ValueError(f"Domain pack references unknown tools: {missing}. Available tools: {list_registered_tools()}")
    return [TOOL_REGISTRY[n] for n in names]


def clear_registry_for_testing():
    """Utility helper to reset registry state in isolated tests."""
    TOOL_REGISTRY.clear()
