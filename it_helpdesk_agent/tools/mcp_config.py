import os
from mcp import StdioServerParameters
from google.adk.tools import McpToolset
from google.adk.tools.mcp_tool import StdioConnectionParams

def get_enterprise_rag_mcp_toolset():
    """
    Connects to the Enterprise RAG MCP server for ERP, HRM, and CRM knowledge retrieval.
    Runs locally as a subprocess using stdio transport with absolute path resolution.
    """
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
