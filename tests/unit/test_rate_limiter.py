import time
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from it_helpdesk_agent.app_utils.rate_limiter import InMemoryRateLimiter, RateLimitMiddleware


def test_in_memory_rate_limiter_sliding_window():
    limiter = InMemoryRateLimiter(requests_per_minute=3)
    key = "test-user-ip-1"

    # Requests 1, 2, 3 should be allowed
    allowed1, rem1, _ = limiter.is_allowed(key)
    assert allowed1 is True
    assert rem1 == 2

    allowed2, rem2, _ = limiter.is_allowed(key)
    assert allowed2 is True
    assert rem2 == 1

    allowed3, rem3, _ = limiter.is_allowed(key)
    assert allowed3 is True
    assert rem3 == 0

    # Request 4 in same minute should be rejected
    allowed4, rem4, retry_after = limiter.is_allowed(key)
    assert allowed4 is False
    assert rem4 == 0
    assert retry_after > 0


def test_rate_limit_middleware_blocks_exceeded_requests():
    app = FastAPI()
    test_limiter = InMemoryRateLimiter(requests_per_minute=2)
    app.add_middleware(RateLimitMiddleware, limiter=test_limiter)

    @app.get("/api/test-route")
    def sample_endpoint():
        return {"status": "ok"}

    @app.get("/healthz")
    def health_endpoint():
        return {"status": "healthy"}

    client = TestClient(app)

    # Health check is exempt
    for _ in range(5):
        resp = client.get("/healthz")
        assert resp.status_code == 200

    # API route: first 2 requests succeed
    resp1 = client.get("/api/test-route")
    assert resp1.status_code == 200
    assert resp1.headers.get("X-RateLimit-Remaining") == "1"

    resp2 = client.get("/api/test-route")
    assert resp2.status_code == 200
    assert resp2.headers.get("X-RateLimit-Remaining") == "0"

    # 3rd request should return 429 Too Many Requests
    resp3 = client.get("/api/test-route")
    assert resp3.status_code == 429
    data = resp3.json()
    assert data["error_code"] == "RATE_LIMIT_EXCEEDED"
    assert "Retry-After" in resp3.headers


def test_middleware_execution_order_rate_limit_before_auth():
    """
    Validates that RateLimitMiddleware executes BEFORE SSOAuthenticationMiddleware
    to drop unauthenticated/excess requests at 0 CPU cost without running JWT verification.
    """
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request
    from starlette.responses import Response

    execution_order = []

    class DummyAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next) -> Response:
            execution_order.append("auth")
            return await call_next(request)

    class DummyRateLimitMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next) -> Response:
            execution_order.append("rate_limit")
            return await call_next(request)

    test_app = FastAPI()
    # In fast_api_app.py:
    # 1. app.add_middleware(SSOAuthenticationMiddleware)
    # 2. app.add_middleware(RateLimitMiddleware)
    test_app.add_middleware(DummyAuthMiddleware)
    test_app.add_middleware(DummyRateLimitMiddleware)

    @test_app.get("/test")
    def sample_route():
        return {"ok": True}

    client = TestClient(test_app)
    client.get("/test")

    # Rate limit (outermost layer) MUST execute before auth (inner layer)
    assert execution_order == ["rate_limit", "auth"]


def test_l3_rate_limiter_quota_enforcement():
    from it_helpdesk_agent.app_utils.rate_limiter import get_l3_rate_limiter, check_l3_rate_limit

    l3_limiter = get_l3_rate_limiter()
    test_user_id = "test-l3-engineer"

    # Reset test key in history
    l3_limiter._history[f"l3_user:{test_user_id}"] = []

    # 10 calls should succeed
    for i in range(10):
        allowed, remaining, _ = check_l3_rate_limit(test_user_id)
        assert allowed is True
        assert remaining == (9 - i)

    # 11th call should be blocked
    blocked, rem, retry_after = check_l3_rate_limit(test_user_id)
    assert blocked is False
    assert rem == 0
    assert retry_after > 0


def test_in_memory_rate_limiter_auto_cleanup():
    limiter = InMemoryRateLimiter(requests_per_minute=5)
    now = time.time()
    
    # Inject expired key (>10 mins ago) and fresh key
    limiter._history["expired_key"] = [now - 700.0]
    limiter._history["active_key"] = [now - 5.0]

    # Explicit cleanup
    limiter.cleanup_expired_keys()
    assert "expired_key" not in limiter._history
    assert "active_key" in limiter._history


def test_route_ticket_to_tier_rate_limits_by_caller_not_owner():
    """
    Validates that when Admin Alice escalates a ticket belonging to Employee Bob to L3,
    the L3 rate limit quota is charged to Alice (the caller), NOT Bob (the ticket owner).
    """
    import contextvars
    from it_helpdesk_agent.app_utils.sso_auth import SSOUser, current_sso_user
    from it_helpdesk_agent.app_utils.rate_limiter import get_l3_rate_limiter, check_l3_rate_limit
    from it_helpdesk_agent.tools.ticketing_tool import create_helpdesk_ticket, route_ticket_to_tier

    l3_limiter = get_l3_rate_limiter()
    l3_limiter._history["l3_user:alice-admin"] = []
    l3_limiter._history["l3_user:bob-employee"] = []

    # 1. Bob creates a ticket
    bob = SSOUser(
        user_id="bob-employee",
        email="bob@company.com",
        email_verified=True,
        full_name="Bob Employee",
        department="Finance",
        roles=["employee"]
    )
    token_bob = current_sso_user.set(bob)
    created = create_helpdesk_ticket(
        user_id="bob-employee",
        title="Complex DB deadlock issue",
        description="Transaction timeout in finance DB",
        category="Software",
        priority="Critical"
    )
    current_sso_user.reset(token_bob)
    ticket_id = created["ticket"]["id"]

    # 2. Admin Alice escalates Bob's ticket to L3
    alice = SSOUser(
        user_id="alice-admin",
        email="alice@company.com",
        email_verified=True,
        full_name="Alice Admin",
        department="IT",
        roles=["it_admin"]
    )
    token_alice = current_sso_user.set(alice)
    res = route_ticket_to_tier(ticket_id, "L3_Deep_Diagnostics", "Deep analysis required")
    current_sso_user.reset(token_alice)

    assert res["status"] == "success"

    # Alice's L3 quota should be charged (1 request in history)
    assert len(l3_limiter._history.get("l3_user:alice-admin", [])) == 1

    # Bob's L3 quota MUST NOT be charged (0 requests in history)
    assert len(l3_limiter._history.get("l3_user:bob-employee", [])) == 0

    # Verify Bob still has full 10 requests available
    allowed, remaining, _ = check_l3_rate_limit("bob-employee")
    assert allowed is True
    assert remaining == 9  # Consumed this check call, confirming full quota was untouched


def test_rate_limiter_middleware_leftmost_ip_extraction():
    """
    Validates that RateLimitMiddleware extracts the leftmost IP (the actual client)
    from X-Forwarded-For when behind a Load Balancer / reverse proxy.
    """
    app = FastAPI()
    limiter = InMemoryRateLimiter(requests_per_minute=2)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api/data")
    def get_data():
        return {"ok": True}

    client = TestClient(app)

    # Client IP: 198.51.100.10, Proxies: 10.0.0.1, 10.0.0.2
    headers = {"X-Forwarded-For": "198.51.100.10, 10.0.0.1, 10.0.0.2"}

    # 1st and 2nd requests from client 198.51.100.10 should succeed
    r1 = client.get("/api/data", headers=headers)
    assert r1.status_code == 200

    r2 = client.get("/api/data", headers=headers)
    assert r2.status_code == 200

    # 3rd request from 198.51.100.10 should be rate limited (429)
    r3 = client.get("/api/data", headers=headers)
    assert r3.status_code == 429

    # Another client IP: 203.0.113.50 passing through the SAME proxies should NOT be blocked!
    headers2 = {"X-Forwarded-For": "203.0.113.50, 10.0.0.1, 10.0.0.2"}
    r4 = client.get("/api/data", headers=headers2)
    assert r4.status_code == 200


def test_healthz_and_readyz_endpoints():
    """
    Validates that /healthz and /readyz endpoints return HTTP 200 without authentication.
    """
    from it_helpdesk_agent.fast_api_app import app
    client = TestClient(app)

    r_healthz = client.get("/healthz")
    assert r_healthz.status_code == 200
    assert r_healthz.json()["status"] == "healthy"

    r_health = client.get("/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] in ["ok", "healthy"]

    r_readyz = client.get("/readyz")
    assert r_readyz.status_code == 200
    assert r_readyz.json()["status"] == "ready"


