import os
import time
import pytest
from unittest.mock import MagicMock, patch
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from agent_core.app_utils.rate_limiter import (
    InMemoryRateLimiter,
    RateLimitMiddleware,
    check_l3_rate_limit_with_warning,
    reset_rate_limiters,
)
from agent_core.app_utils.token_budget import (
    DeploymentTokenBudgetTracker,
    get_deployment_token_budget_tracker,
    record_token_usage,
    is_budget_exceeded,
    get_monthly_token_usage,
    reset_token_budget_tracker,
)
from agent_core.app_utils.sso_auth import (
    SSOUser,
    get_current_user,
    require_admin,
    current_sso_user,
)
from agent_core.fast_api_app import app as main_app


@pytest.fixture(autouse=True)
def clean_state():
    reset_rate_limiters()
    reset_token_budget_tracker()
    token = current_sso_user.set(None)
    yield
    try:
        current_sso_user.reset(token)
    except Exception:
        current_sso_user.set(None)
    reset_rate_limiters()
    reset_token_budget_tracker()


# ==============================================================================
# 1. 24-Hour Daily Rate Limiting Tests (P1-08)
# ==============================================================================

def test_in_memory_daily_rate_limiting():
    """Validates 24-hour daily sliding window enforcement in InMemoryRateLimiter."""
    limiter = InMemoryRateLimiter(requests_per_minute=100, requests_per_day=3)
    user_id = "test-user-daily"

    # First 3 requests in day allowed
    for i in range(3):
        allowed, remaining, _ = limiter.is_allowed(user_id, window_seconds=86400, max_requests=3)
        assert allowed is True
        assert remaining == 2 - i

    # 4th request rejected
    allowed, remaining, retry_after = limiter.is_allowed(user_id, window_seconds=86400, max_requests=3)
    assert allowed is False
    assert remaining == 0
    assert retry_after > 0


def test_rate_limit_middleware_daily_limit_blocks(monkeypatch):
    """Validates that RateLimitMiddleware enforces RATE_LIMIT_PER_DAY."""
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "100")
    monkeypatch.setenv("RATE_LIMIT_PER_DAY", "2")
    reset_rate_limiters()

    test_app = FastAPI()
    test_app.add_middleware(RateLimitMiddleware)

    @test_app.get("/api/data")
    def sample_endpoint():
        return {"data": "success"}

    client = TestClient(test_app)

    resp1 = client.get("/api/data")
    assert resp1.status_code == 200

    resp2 = client.get("/api/data")
    assert resp2.status_code == 200

    # 3rd request exceeds daily limit (2)
    resp3 = client.get("/api/data")
    assert resp3.status_code == 429
    assert resp3.json()["error_code"] == "RATE_LIMIT_EXCEEDED"
    assert "daily" in resp3.json()["message"].lower() or "ngày" in resp3.json()["message"].lower()


def test_l3_daily_rate_limit_check(monkeypatch):
    """Validates that check_l3_rate_limit_with_warning checks 24h daily L3 limit."""
    monkeypatch.setenv("L3_RATE_LIMIT_PER_MINUTE", "100")
    monkeypatch.setenv("L3_RATE_LIMIT_PER_DAY", "2")
    reset_rate_limiters()

    user_id = "l3_user_001"

    # Request 1 & 2 allowed
    allowed1, rem1, _, is_warn1, _ = check_l3_rate_limit_with_warning(user_id)
    assert allowed1 is True

    allowed2, rem2, _, is_warn2, _ = check_l3_rate_limit_with_warning(user_id)
    assert allowed2 is True

    # Request 3 exceeds daily limit of 2
    allowed3, rem3, retry_after, _, warn_msg = check_l3_rate_limit_with_warning(user_id)
    assert allowed3 is False
    assert rem3 == 0
    assert "24h" in warn_msg or "ngày" in warn_msg


# ==============================================================================
# 2. Deployment Monthly Token Budget & Degrade Mode Tests (P1-08)
# ==============================================================================

def test_monthly_token_budget_tracking(monkeypatch):
    """Validates monthly token accumulation and budget exceeded threshold."""
    monkeypatch.setenv("MONTHLY_TOKEN_BUDGET", "1000")
    reset_token_budget_tracker()

    tracker = get_deployment_token_budget_tracker()
    assert tracker.budget == 1000
    assert tracker.is_budget_exceeded() is False

    # Record 600 tokens
    curr = tracker.record_token_usage(600)
    assert curr == 600
    assert tracker.is_budget_exceeded() is False

    # Record 500 more tokens -> Total 1100 > 1000
    curr = tracker.record_token_usage(500)
    assert curr == 1100
    assert tracker.is_budget_exceeded() is True


def test_monthly_token_budget_alert_deduplication(monkeypatch, caplog):
    """Validates that ALERT log is emitted exactly ONCE per billing cycle when budget exceeded."""
    import logging
    monkeypatch.setenv("MONTHLY_TOKEN_BUDGET", "500")
    reset_token_budget_tracker()

    tracker = get_deployment_token_budget_tracker()

    with caplog.at_level(logging.CRITICAL):
        # First breach
        tracker.record_token_usage(600)
        assert tracker.is_budget_exceeded() is True

        # Second breach call
        tracker.record_token_usage(200)
        assert tracker.is_budget_exceeded() is True

    # Verify critical log was emitted exactly 1 time
    alert_logs = [r for r in caplog.records if "ALERT: Monthly deployment token budget exceeded" in r.message]
    assert len(alert_logs) == 1


async def test_runtime_degrade_mode_blocks_l3(monkeypatch):
    """Validates that in runtime.py, when monthly token budget is exceeded, L3 is blocked."""
    monkeypatch.setenv("MONTHLY_TOKEN_BUDGET", "100")
    reset_token_budget_tracker()

    from agent_core.app_utils.token_budget import record_token_usage
    record_token_usage(150)  # Exceeds 100

    from agent_core.runtime import semantic_cache_before_model_callback
    from google.genai import types

    mock_req = types.GenerateContentConfig()
    mock_ctx = MagicMock()
    mock_agent = MagicMock()
    mock_agent.name = "l3_deep_diagnostics_agent"
    mock_ctx._invocation_context.agent = mock_agent

    # Call before callback for L3 agent
    resp = await semantic_cache_before_model_callback(mock_ctx, mock_req)
    assert resp is not None
    assert resp.custom_metadata.get("degrade_mode") is True
    assert "Degrade Mode" in resp.content.parts[0].text


# ==============================================================================
# 3. Sensitive Endpoint Protection with require_admin (P1-11)
# ==============================================================================

def test_require_admin_dependency():
    """Validates require_admin dependency rejects non-admin users and allows admin users."""
    from fastapi import HTTPException

    # Non-admin user
    employee_user = SSOUser(
        user_id="emp-01",
        email="emp@example.com",
        email_verified=True,
        roles=["employee", "user"],
        is_authenticated=True,
    )

    import asyncio
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(require_admin(employee_user))
    assert exc_info.value.status_code == 403

    # Admin user
    admin_user = SSOUser(
        user_id="admin-01",
        email="admin@example.com",
        email_verified=True,
        roles=["employee", "it_admin"],
        is_authenticated=True,
    )
    res = asyncio.run(require_admin(admin_user))
    assert res.user_id == "admin-01"


def test_analytics_endpoints_require_admin_and_return_scope(monkeypatch):
    """
    Validates that:
    1. /api/analytics/instance-summary and /api/analytics/summary require admin authentication.
    2. Response includes "scope": "single_instance".
    """
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_LOCAL_DEV_SSO", "true")
    monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", True)
    from agent_core.app_utils.sso_auth import create_dev_mock_token
    client = TestClient(main_app)

    # 1. Non-admin user
    non_admin = SSOUser(
        user_id="user_reg",
        email="user@example.com",
        roles=["employee"],
        is_authenticated=True,
    )
    token_non_admin = create_dev_mock_token(non_admin)
    headers_non_admin = {"Authorization": f"Bearer {token_non_admin}"}

    resp = client.get("/api/analytics/instance-summary", headers=headers_non_admin)
    assert resp.status_code == 403

    resp_old = client.get("/api/analytics/summary", headers=headers_non_admin)
    assert resp_old.status_code == 403

    # 2. Admin user
    admin = SSOUser(
        user_id="admin_user",
        email="admin@example.com",
        roles=["it_admin"],
        is_authenticated=True,
    )
    token_admin = create_dev_mock_token(admin)
    headers_admin = {"Authorization": f"Bearer {token_admin}"}

    resp_inst = client.get("/api/analytics/instance-summary", headers=headers_admin)
    assert resp_inst.status_code == 200
    data = resp_inst.json()
    assert data.get("scope") == "single_instance"

    resp_sum = client.get("/api/analytics/summary", headers=headers_admin)
    assert resp_sum.status_code == 200
    assert resp_sum.json().get("scope") == "single_instance"


# ==============================================================================
# 4. Cache Query Threshold Enforcement (P1-11)
# ==============================================================================

def test_cache_query_threshold_validation(monkeypatch):
    """Validates that /api/cache/query rejects threshold < 0.85 with HTTP 400 Bad Request."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("ALLOW_LOCAL_DEV_SSO", "true")
    monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", True)
    from agent_core.app_utils.sso_auth import create_dev_mock_token
    client = TestClient(main_app)
    user = SSOUser(
        user_id="test_user",
        email="test@example.com",
        roles=["employee"],
        is_authenticated=True,
    )
    token = create_dev_mock_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    # Threshold < 0.85 should be rejected
    resp_invalid = client.get("/api/cache/query", params={"q": "wifi password", "threshold": 0.80}, headers=headers)
    assert resp_invalid.status_code == 400
    assert "0.85" in resp_invalid.json()["detail"]

    # Threshold >= 0.85 should be accepted
    resp_valid = client.get("/api/cache/query", params={"q": "wifi password", "threshold": 0.85}, headers=headers)
    assert resp_valid.status_code == 200

    resp_valid2 = client.get("/api/cache/query", params={"q": "wifi password", "threshold": 0.92}, headers=headers)
    assert resp_valid2.status_code == 200

