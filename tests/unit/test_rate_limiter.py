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
