import os
import time
import threading
from typing import Optional
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class InMemoryRateLimiter:
    """
    Thread-safe Sliding Window Rate Limiter.
    Protects against denial-of-wallet, bot abuse, and runaway LLM reasoning costs.
    """

    def __init__(self, requests_per_minute: int = 60, burst_limit: Optional[int] = None):
        self.requests_per_minute = int(os.getenv("RATE_LIMIT_PER_MINUTE", requests_per_minute))
        self.burst_limit = burst_limit or (self.requests_per_minute * 2)
        self._window_seconds = 60.0
        self._history: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str, max_requests: Optional[int] = None) -> tuple[bool, int, float]:
        """
        Evaluates whether a request for `key` is allowed.
        Returns: (allowed: bool, remaining_requests: int, reset_after_seconds: float)
        """
        limit = max_requests or self.requests_per_minute
        now = time.time()
        window_start = now - self._window_seconds

        with self._lock:
            # Clean up older timestamps for this key
            timestamps = self._history.get(key, [])
            valid_timestamps = [t for t in timestamps if t > window_start]

            if len(valid_timestamps) >= limit:
                oldest_in_window = valid_timestamps[0]
                reset_after = max(0.0, (oldest_in_window + self._window_seconds) - now)
                self._history[key] = valid_timestamps
                return False, 0, round(reset_after, 2)

            valid_timestamps.append(now)
            self._history[key] = valid_timestamps
            remaining = limit - len(valid_timestamps)
            return True, remaining, 0.0

    def cleanup_expired_keys(self) -> None:
        """Evicts keys that have had no traffic in the last 10 minutes to bound memory usage."""
        now = time.time()
        expiry = now - 600.0
        with self._lock:
            keys_to_remove = [k for k, ts in self._history.items() if not ts or ts[-1] < expiry]
            for k in keys_to_remove:
                del self._history[k]


_global_rate_limiter = InMemoryRateLimiter()
_l3_rate_limiter = InMemoryRateLimiter(requests_per_minute=10)


def get_global_rate_limiter() -> InMemoryRateLimiter:
    return _global_rate_limiter


def get_l3_rate_limiter() -> InMemoryRateLimiter:
    return _l3_rate_limiter


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI Middleware enforcing client rate limits per IP and SSO user.
    """

    EXEMPT_PATHS = {"/", "/healthz", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}

    def __init__(self, app, limiter: Optional[InMemoryRateLimiter] = None):
        super().__init__(app)
        self.limiter = limiter or get_global_rate_limiter()

    async def dispatch(self, request: Request, call_next) -> Response:
        # Skip rate-limiting for health checks and doc endpoints
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        # Rate limit enabled check
        if os.getenv("RATE_LIMIT_ENABLED", "true").lower() not in ("true", "1", "yes"):
            return await call_next(request)

        # Client identifier: Forwarded IP or client host
        forwarded = request.headers.get("X-Forwarded-For")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
        
        # User authorization header hash or IP
        auth_header = request.headers.get("Authorization", "")
        key = f"ip:{client_ip}" if not auth_header else f"auth:{hash(auth_header)}"

        allowed, remaining, retry_after = self.limiter.is_allowed(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "status": "error",
                    "error_code": "RATE_LIMIT_EXCEEDED",
                    "message": f"Quá số lượng yêu cầu cho phép (Rate limit exceeded). Vui lòng thử lại sau {retry_after}s.",
                    "retry_after_seconds": retry_after
                },
                headers={"Retry-After": str(int(retry_after) + 1)}
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
