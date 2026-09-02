"""
DEPRECATED: MCP configuration module.
Maintained for backwards compatibility with legacy external MCP tool integrations.
Canonical tools and knowledge retrieval functions are dynamically registered in
`agent_core.tools.registry` and resolved via Domain Packs (`agents.yaml` / `pack.yaml`).
"""
import os
from typing import Optional
from mcp import StdioServerParameters
from google.adk.tools import McpToolset
from google.adk.tools.mcp_tool import StreamableHTTPConnectionParams, StdioConnectionParams


def get_auth_headers(readonly_context=None) -> dict[str, str]:
    """Dynamically resolves Authorization Bearer header for current SSO user context."""
    try:
        from agent_core.app_utils.sso_auth import get_current_sso_token
        token = get_current_sso_token()
        if token:
            return {"Authorization": f"Bearer {token}"}
    except Exception:
        pass
    return {}


def get_enterprise_rag_mcp_toolset() -> McpToolset:
    """
    Connects to the Enterprise RAG MCP server for ERP, HRM, and CRM knowledge retrieval.
    Defaults to Streamable-HTTP transport with OIDC token forwarding via headers.
    """
    transport = os.getenv("MCP_TRANSPORT", "streamable-http").lower()
    mcp_url = os.getenv("ENTERPRISE_RAG_MCP_URL", "http://127.0.0.1:8001/mcp")

    if transport in ("streamable-http", "http", "streamable_http"):
        return McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=mcp_url,
                timeout=120.0,
                headers=get_auth_headers(),
            ),
            header_provider=get_auth_headers,
        )

    # Subprocess fallback for isolated CLI debugging if explicitly set to 'stdio'
    tools_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(tools_dir, "enterprise_rag_mcp", "main.py")
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "LANG": "en_US.UTF-8"
    }

    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command="uv",
                args=["run", script_path],
                env=env
            ),
            timeout=120.0
        )
    )
