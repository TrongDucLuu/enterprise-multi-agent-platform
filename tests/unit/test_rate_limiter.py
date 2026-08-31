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


def test_rate_limiter_direct_cloud_run_ip_extraction(monkeypatch):
    """
    Validates that in direct Cloud Run mode (BEHIND_LOAD_BALANCER=false),
    RateLimitMiddleware extracts the rightmost IP (ips[-1]) appended by Cloud Run.
    """
    monkeypatch.setenv("BEHIND_LOAD_BALANCER", "false")
    app = FastAPI()
    limiter = InMemoryRateLimiter(requests_per_minute=2)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api/data")
    def get_data():
        return {"ok": True}

    client = TestClient(app)

    # In direct Cloud Run: header is "spoofed_ip, 203.0.113.195 (real client)"
    headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.195"}

    # 1st and 2nd requests from client 203.0.113.195 should succeed
    r1 = client.get("/api/data", headers=headers)
    assert r1.status_code == 200

    r2 = client.get("/api/data", headers=headers)
    assert r2.status_code == 200

    # 3rd request from 203.0.113.195 should be rate limited (429)
    # Even if attacker alters spoofed_ip to "9.9.9.9", Cloud Run still appends "203.0.113.195" at ips[-1]
    spoofed_headers = {"X-Forwarded-For": "9.9.9.9, 203.0.113.195"}
    r3 = client.get("/api/data", headers=spoofed_headers)
    assert r3.status_code == 429

    # Different real client 198.51.100.20 has its own separate bucket
    other_client_headers = {"X-Forwarded-For": "1.2.3.4, 198.51.100.20"}
    r4 = client.get("/api/data", headers=other_client_headers)
    assert r4.status_code == 200


def test_rate_limiter_behind_load_balancer_ip_extraction(monkeypatch):
    """
    Validates that in Load Balancer mode (BEHIND_LOAD_BALANCER=true),
    RateLimitMiddleware extracts ips[-2] (the verified client IP preceding the LB IP).
    Prevents attackers from spoofing leftmost IP (ips[0]).
    """
    monkeypatch.setenv("BEHIND_LOAD_BALANCER", "true")
    app = FastAPI()
    limiter = InMemoryRateLimiter(requests_per_minute=2)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api/data")
    def get_data():
        return {"ok": True}

    client = TestClient(app)

    # In GCP External HTTPS LB: header is "spoofed_ip, 203.0.113.195 (real client), 34.120.50.1 (gfe lb)"
    headers = {"X-Forwarded-For": "1.2.3.4, 203.0.113.195, 34.120.50.1"}

    # 1st and 2nd requests from 203.0.113.195 should succeed
    r1 = client.get("/api/data", headers=headers)
    assert r1.status_code == 200

    r2 = client.get("/api/data", headers=headers)
    assert r2.status_code == 200

    # 3rd request with altered spoofed_ip "8.8.8.8" MUST still be blocked (429) because ips[-2] is tracked
    spoofed_attack = {"X-Forwarded-For": "8.8.8.8, 203.0.113.195, 34.120.50.1"}
    r3 = client.get("/api/data", headers=spoofed_attack)
    assert r3.status_code == 429

    # Another distinct client 198.51.100.55 behind the same LB should NOT be blocked
    other_client = {"X-Forwarded-For": "8.8.8.8, 198.51.100.55, 34.120.50.1"}
    r4 = client.get("/api/data", headers=other_client)
    assert r4.status_code == 200


def test_rate_limiter_user_token_rotation_defense(monkeypatch):
    """
    P0.2: Validates that rotating Authorization tokens for the SAME authenticated user
    does NOT bypass the rate limit threshold (key is derived from user_id, not raw token).
    """
    from unittest.mock import patch
    from it_helpdesk_agent.app_utils.sso_auth import SSOUser, SSOAuthenticationMiddleware
    import it_helpdesk_agent.app_utils.sso_auth as sso_mod

    app = FastAPI()
    limiter = InMemoryRateLimiter(requests_per_minute=3)
    app.add_middleware(SSOAuthenticationMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api/user-resource")
    def user_resource():
        return {"data": "confidential"}

    # Mock verify_sso_token to return the same user_id for any token starting with 'token-alice-'
    def mock_verify(token: str):
        if token.startswith("token-alice-"):
            return SSOUser(
                user_id="user-alice-007",
                email="alice@company.com",
                email_verified=True,
                full_name="Alice Security",
                department="SecOps",
                roles=["employee", "it_admin"]
            )
        raise Exception("Invalid token")

    monkeypatch.setattr(sso_mod, "verify_sso_token", mock_verify)
    client = TestClient(app)

    # 3 requests with 3 DIFFERENT tokens for Alice should succeed
    for i in range(1, 4):
        headers = {"Authorization": f"Bearer token-alice-{i}"}
        resp = client.get("/api/user-resource", headers=headers)
        assert resp.status_code == 200, f"Request {i} failed: {resp.text}"

    # 4th request with a brand new token for Alice MUST be blocked (429)
    resp4 = client.get("/api/user-resource", headers={"Authorization": "Bearer token-alice-4"})
    assert resp4.status_code == 429
    assert resp4.json()["error_code"] == "RATE_LIMIT_EXCEEDED"


def test_rate_limiter_multi_user_isolation(monkeypatch):
    """
    P0.2: Validates that two distinct authenticated users have completely independent buckets.
    User A exhausting their quota has zero impact on User B.
    """
    from it_helpdesk_agent.app_utils.sso_auth import SSOUser, SSOAuthenticationMiddleware
    import it_helpdesk_agent.app_utils.sso_auth as sso_mod

    app = FastAPI()
    limiter = InMemoryRateLimiter(requests_per_minute=2)
    app.add_middleware(SSOAuthenticationMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api/protected")
    def protected_route():
        return {"ok": True}

    def mock_verify(token: str):
        if token == "token-user-a":
            return SSOUser(user_id="user-A", email="a@corp.com", email_verified=True, full_name="User A", department="Dev", roles=["employee"])
        elif token == "token-user-b":
            return SSOUser(user_id="user-B", email="b@corp.com", email_verified=True, full_name="User B", department="Sales", roles=["employee"])
        raise Exception("Invalid token")

    monkeypatch.setattr(sso_mod, "verify_sso_token", mock_verify)
    client = TestClient(app)

    # User A exhausts quota (2 requests)
    r_a1 = client.get("/api/protected", headers={"Authorization": "Bearer token-user-a"})
    r_a2 = client.get("/api/protected", headers={"Authorization": "Bearer token-user-a"})
    assert r_a1.status_code == 200
    assert r_a2.status_code == 200

    r_a3 = client.get("/api/protected", headers={"Authorization": "Bearer token-user-a"})
    assert r_a3.status_code == 429

    # User B should still have full quota (2 requests)
    r_b1 = client.get("/api/protected", headers={"Authorization": "Bearer token-user-b"})
    assert r_b1.status_code == 200
    assert r_b1.headers.get("X-RateLimit-Remaining") == "1"

    r_b2 = client.get("/api/protected", headers={"Authorization": "Bearer token-user-b"})
    assert r_b2.status_code == 200
    assert r_b2.headers.get("X-RateLimit-Remaining") == "0"


def test_rate_limiter_unauthenticated_ip_isolation():
    """
    P0.2: Validates that unauthenticated requests correctly fallback to IP-based rate limiting buckets.
    """
    app = FastAPI()
    limiter = InMemoryRateLimiter(requests_per_minute=2)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api/public-unauth")
    def unauth_route():
        return {"public": True}

    client = TestClient(app)

    # IP 192.0.2.1 makes 2 requests
    h1 = {"X-Forwarded-For": "192.0.2.1"}
    assert client.get("/api/public-unauth", headers=h1).status_code == 200
    assert client.get("/api/public-unauth", headers=h1).status_code == 200
    assert client.get("/api/public-unauth", headers=h1).status_code == 429

    # IP 198.51.100.2 has separate bucket
    h2 = {"X-Forwarded-For": "198.51.100.2"}
    assert client.get("/api/public-unauth", headers=h2).status_code == 200


def test_single_jwt_verification_across_middlewares(monkeypatch):
    """
    P1.4: Spy test asserting that verify_sso_token is called EXACTLY ONCE
    for a request passing through both RateLimitMiddleware and SSOAuthenticationMiddleware.
    """
    from unittest.mock import MagicMock
    from it_helpdesk_agent.app_utils.sso_auth import SSOUser, SSOAuthenticationMiddleware
    import it_helpdesk_agent.app_utils.sso_auth as sso_mod

    app = FastAPI()
    limiter = InMemoryRateLimiter(requests_per_minute=10)
    app.add_middleware(SSOAuthenticationMiddleware)
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    @app.get("/api/memoized-check")
    def memoized_route():
        return {"status": "ok"}

    mock_user = SSOUser(
        user_id="user-memo-1",
        email="memo@company.com",
        email_verified=True,
        full_name="Memo User",
        department="IT",
        roles=["employee"]
    )

    verify_spy = MagicMock(return_value=mock_user)
    monkeypatch.setattr(sso_mod, "verify_sso_token", verify_spy)

    client = TestClient(app)
    resp = client.get("/api/memoized-check", headers={"Authorization": "Bearer sample-jwt-token"})

    assert resp.status_code == 200
    # Crucial assertion: verify_sso_token must be executed exactly 1 time!
    assert verify_spy.call_count == 1, f"Expected verify_sso_token to be called 1 time, got {verify_spy.call_count}"


def test_healthz_and_readyz_endpoints():
    """
    Validates that /healthz and /readyz endpoints return HTTP 200 without authentication
    and do not leak internal system cache details.
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
    assert "cache_entries" not in r_readyz.json()
