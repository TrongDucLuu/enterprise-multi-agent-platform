"""
Unit tests for Agent-to-Agent (A2A) fail-loud behavior across environments.

Verifies:
1. ENVIRONMENT=production: A2A setup failure raises RuntimeError (crashes pod / fails fast).
2. ENVIRONMENT=development: A2A setup failure marks status as degraded and /readyz returns 503.
3. ENABLE_A2A_ENDPOINT=false: /readyz returns 200 with a2a_status='disabled'.
4. ENABLE_A2A_ENDPOINT=true (successful): /readyz returns 200 with a2a_status='healthy'.
"""

import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient


def test_a2a_fail_loud_production_raises_runtime_error(monkeypatch):
    """
    In production mode, any failure during A2A endpoint initialization MUST raise RuntimeError
    to immediately crash the container pod (fail fast).
    """
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ENABLE_A2A_ENDPOINT", "true")

    from agent_core.fast_api_app import setup_a2a_endpoint
    mock_app = MagicMock()

    with patch("agent_core.agent_builder.build_agent_system", side_effect=Exception("Failed to construct agent hierarchy")):
        with pytest.raises(RuntimeError, match="A2A Protocol endpoint initialization failed in production"):
            setup_a2a_endpoint(mock_app)


def test_a2a_fail_loud_development_degrades_and_returns_503(monkeypatch):
    """
    In development mode, an A2A setup failure should not crash the process, but should mark
    the A2A status as 'degraded' and return 503 Service Unavailable on /readyz.
    """
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENABLE_A2A_ENDPOINT", "true")
    monkeypatch.setenv("ALLOW_LOCAL_DEV_SSO", "true")

    from agent_core.fast_api_app import app, setup_a2a_endpoint, get_a2a_status

    with patch("agent_core.agent_builder.build_agent_system", side_effect=Exception("Dev mock failure in A2A")):
        res = setup_a2a_endpoint(app)
        assert res is None

    status_val, err_msg = get_a2a_status()
    assert status_val == "degraded"
    assert "Dev mock failure in A2A" in (err_msg or "")

    client = TestClient(app)
    response = client.get("/readyz")
    assert response.status_code == 503
    assert "Service degraded: A2A endpoint failed to initialize" in response.json()["detail"]


def test_a2a_disabled_returns_200_with_disabled_status(monkeypatch):
    """
    When ENABLE_A2A_ENDPOINT=false, setup_a2a_endpoint returns None, status is disabled,
    and /readyz returns 200 OK.
    """
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ENABLE_A2A_ENDPOINT", "false")
    monkeypatch.setenv("ALLOW_LOCAL_DEV_SSO", "true")

    from agent_core.fast_api_app import app, setup_a2a_endpoint, get_a2a_status

    setup_a2a_endpoint(app)
    status_val, err_msg = get_a2a_status()
    assert status_val == "disabled"
    assert err_msg is None

    client = TestClient(app)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json()["a2a_status"] == "disabled"
