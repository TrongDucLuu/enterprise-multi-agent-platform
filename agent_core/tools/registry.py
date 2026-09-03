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


def get_active_tool_aliases() -> dict[str, str]:
    """Retrieves tool aliases from the active domain pack configuration."""
    try:
        from agent_core.agent_builder import load_domain_pack
        pack_info = load_domain_pack()
        return pack_info.get("pack_meta", {}).get("tool_aliases", {}) or {}
    except Exception:
        return {}


def resolve_tools(names: list[str], tool_aliases: Optional[dict[str, str]] = None) -> list[object]:
    """
    Resolves a list of tool names to their registered callable functions or built-in tool instances.
    If tool_aliases is provided (or loaded from domain pack), resolves aliases to canonical tool names first.
    Raises ValueError if any tool is unknown (fail-closed).
    """
    if tool_aliases is None:
        tool_aliases = get_active_tool_aliases()

    resolved = []
    missing = []
    for n in names:
        target_name = tool_aliases.get(n, n)
        if target_name in ("builtin:preload_memory", "preload_memory"):
            try:
                from google.adk.tools import preload_memory_tool
                resolved.append(preload_memory_tool.PreloadMemoryTool())
            except Exception as e:
                logger.error("Failed to instantiate PreloadMemoryTool: %s", e)
                raise
        elif target_name in TOOL_REGISTRY:
            resolved.append(TOOL_REGISTRY[target_name])
        elif n in TOOL_REGISTRY:
            resolved.append(TOOL_REGISTRY[n])
        else:
            missing.append(n)

    if missing:
        available = list_registered_tools() + ["builtin:preload_memory"]
        raise ValueError(f"Domain pack references unknown tools: {missing}. Available tools: {available}")
    return resolved


def clear_registry_for_testing():
    """Utility helper to reset registry state in isolated tests."""
    TOOL_REGISTRY.clear()
