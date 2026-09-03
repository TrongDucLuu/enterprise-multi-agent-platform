"""
Unit tests for Agent-to-Agent (A2A) protocol endpoint integration in agent_core.
Verifies:
1. Feature flag gating (ENABLE_A2A_ENDPOINT).
2. Parent middleware enforcement (SSOAuthenticationMiddleware: 401 on missing auth, RateLimitMiddleware).
3. Dynamic AgentCard generation matching active domain pack (it-helpdesk vs _template).
4. Sub-app routing and Agent Card JSON schema compliance.
"""

import os
import pytest
from fastapi.testclient import TestClient
from agent_core.app_utils.sso_auth import SSOUser, create_dev_mock_token


@pytest.fixture(autouse=True)
def reset_env(monkeypatch):
    """Ensure clean environment state for each test."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_LOCAL_DEV_SSO", "true")
    monkeypatch.setenv("USE_IN_MEMORY_SESSION", "true")
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "in_memory")
    monkeypatch.setenv("ALLOWED_DOMAINS", "company.com")


def test_a2a_endpoint_disabled_by_default(monkeypatch):
    """When ENABLE_A2A_ENDPOINT is not set / false, /a2a routes return 404."""
    monkeypatch.setenv("ENABLE_A2A_ENDPOINT", "false")
    
    from agent_core.fast_api_app import app
    client = TestClient(app)

    user = SSOUser(user_id="u1", email="u1@company.com", roles=["employee"], is_authenticated=True)
    token = create_dev_mock_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/a2a/.well-known/agent-card.json", headers=headers)
    assert resp.status_code == 404


def test_a2a_endpoint_requires_sso_authentication(monkeypatch):
    """When ENABLE_A2A_ENDPOINT is true, unauthenticated calls to /a2a are blocked with 401."""
    monkeypatch.setenv("ENABLE_A2A_ENDPOINT", "true")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOW_LOCAL_DEV_SSO", "false")

    from fastapi import FastAPI
    from agent_core.fast_api_app import setup_a2a_endpoint
    from agent_core.app_utils.sso_auth import SSOAuthenticationMiddleware
    from agent_core.app_utils.rate_limiter import RateLimitMiddleware

    test_app = FastAPI()
    test_app.add_middleware(SSOAuthenticationMiddleware)
    test_app.add_middleware(RateLimitMiddleware)

    setup_a2a_endpoint(test_app)
    client = TestClient(test_app)

    # 1. Unauthenticated request -> 401 Unauthorized
    resp = client.get("/a2a/.well-known/agent-card.json")
    assert resp.status_code == 401
    assert "Missing Authorization Bearer header" in resp.json().get("detail", "")


def test_a2a_agent_card_returns_valid_json_with_auth(monkeypatch):
    """Authenticated request returns valid A2A AgentCard with correct metadata."""
    monkeypatch.setenv("ENABLE_A2A_ENDPOINT", "true")
    monkeypatch.setenv("DOMAIN_PACK", "it-helpdesk")

    from fastapi import FastAPI
    from agent_core.fast_api_app import setup_a2a_endpoint
    from agent_core.app_utils.sso_auth import SSOAuthenticationMiddleware
    from agent_core.app_utils.rate_limiter import RateLimitMiddleware

    test_app = FastAPI()
    test_app.add_middleware(SSOAuthenticationMiddleware)
    test_app.add_middleware(RateLimitMiddleware)

    setup_a2a_endpoint(test_app)
    client = TestClient(test_app)

    user = SSOUser(user_id="u1", email="u1@company.com", roles=["employee"], is_authenticated=True)
    token = create_dev_mock_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/a2a/.well-known/agent-card.json", headers=headers)
    assert resp.status_code == 200
    card_data = resp.json()

    assert "name" in card_data
    assert "description" in card_data
    assert "skills" in card_data
    assert len(card_data["skills"]) >= 20

    skill_names = [s["name"] for s in card_data["skills"]]
    assert any("create_case" in name for name in skill_names)
    assert any("get_case" in name for name in skill_names)


def test_a2a_agent_card_dynamic_template_domain_pack(monkeypatch):
    """With DOMAIN_PACK=_template, AgentCard declares skills corresponding to the template pack."""
    monkeypatch.setenv("ENABLE_A2A_ENDPOINT", "true")
    monkeypatch.setenv("DOMAIN_PACK", "_template")

    from fastapi import FastAPI
    from agent_core.fast_api_app import setup_a2a_endpoint
    from agent_core.app_utils.sso_auth import SSOAuthenticationMiddleware
    from agent_core.app_utils.rate_limiter import RateLimitMiddleware

    test_app = FastAPI()
    test_app.add_middleware(SSOAuthenticationMiddleware)
    test_app.add_middleware(RateLimitMiddleware)

    setup_a2a_endpoint(test_app)
    client = TestClient(test_app)

    user = SSOUser(user_id="u1", email="u1@company.com", roles=["employee"], is_authenticated=True)
    token = create_dev_mock_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.get("/a2a/.well-known/agent-card.json", headers=headers)
    assert resp.status_code == 200
    card_data = resp.json()

    assert card_data["name"] == "template_starter_agent" or "template" in card_data["name"]
    skill_names = [s["name"] for s in card_data["skills"]]
    assert any("specialist_agent" in name for name in skill_names)
