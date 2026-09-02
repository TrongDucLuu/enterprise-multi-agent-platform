import pytest
from starlette.testclient import TestClient

from agent_core.app_utils.sso_auth import (
    SSOUser,
    create_dev_mock_token,
    current_sso_user,
    current_sso_raw_token,
    get_current_sso_token,
)
from agent_core.tools.enterprise_rag_mcp.main import get_mcp_app
from agent_core.tools.mcp_config import get_auth_headers, get_enterprise_rag_mcp_toolset
from google.adk.tools.mcp_tool import StreamableHTTPConnectionParams


def test_get_auth_headers_empty_when_no_token(monkeypatch):
    monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
    token_ctx = current_sso_raw_token.set(None)
    user_ctx = current_sso_user.set(None)
    try:
        headers = get_auth_headers()
        assert headers == {}
    finally:
        current_sso_raw_token.reset(token_ctx)
        current_sso_user.reset(user_ctx)


def test_get_auth_headers_forwards_bearer_token():
    token_ctx = current_sso_raw_token.set("test-jwt-token-xyz")
    try:
        headers = get_auth_headers()
        assert headers == {"Authorization": "Bearer test-jwt-token-xyz"}
    finally:
        current_sso_raw_token.reset(token_ctx)


def test_get_enterprise_rag_mcp_toolset_uses_streamable_http(monkeypatch):
    monkeypatch.setenv("MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("ENTERPRISE_RAG_MCP_URL", "http://rag-service:8001/mcp")

    toolset = get_enterprise_rag_mcp_toolset()
    assert isinstance(toolset._connection_params, StreamableHTTPConnectionParams)
    assert toolset._connection_params.url == "http://rag-service:8001/mcp"
    assert toolset._header_provider is not None


def test_mcp_auth_middleware_rejects_missing_token_in_prod(monkeypatch):
    monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
    app = get_mcp_app()
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
                "id": 1,
            },
        )
        assert response.status_code == 401
        assert "Missing Authorization" in response.json().get("detail", "")


def test_mcp_auth_middleware_accepts_valid_token(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_LOCAL_DEV_SSO", "true")
    monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", True)
    test_user = SSOUser(
        user_id="it-admin-99",
        email="itadmin@company.com",
        roles=["employee", "it_admin"],
        department="IT",
    )
    valid_token = create_dev_mock_token(test_user)

    app = get_mcp_app()
    with TestClient(app) as client:
        response = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {valid_token}",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"},
                },
                "id": 1,
            },
        )
        assert response.status_code == 200
        assert "mcp-session-id" in response.headers


def test_mcp_health_endpoints_bypass_auth(monkeypatch):
    monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
    app = get_mcp_app()
    with TestClient(app) as client:
        res = client.get("/healthz")
        assert res.status_code != 401
