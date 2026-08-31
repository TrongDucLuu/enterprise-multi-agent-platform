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

