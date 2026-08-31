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
        self._last_cleanup = time.time()
        self._op_count = 0

    def is_allowed(self, key: str, max_requests: Optional[int] = None) -> tuple[bool, int, float]:
        """
        Evaluates whether a request for `key` is allowed.
        Returns: (allowed: bool, remaining_requests: int, reset_after_seconds: float)
        """
        limit = max_requests or self.requests_per_minute
        now = time.time()
        window_start = now - self._window_seconds

        with self._lock:
            # Auto-clean expired entries periodically to prevent unbounded memory growth
            self._op_count += 1
            if self._op_count >= 50 or (now - self._last_cleanup) > 60.0:
                self._cleanup_expired_keys_internal(now)
                self._last_cleanup = now
                self._op_count = 0

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

    def _cleanup_expired_keys_internal(self, now: float) -> None:
        """Internal helper to evict keys inactive for > 10 minutes (called under lock)."""
        expiry = now - 600.0
        keys_to_remove = [k for k, ts in self._history.items() if not ts or ts[-1] < expiry]
        for k in keys_to_remove:
            del self._history[k]

    def cleanup_expired_keys(self) -> None:
        """Explicit eviction of inactive keys."""
        with self._lock:
            self._cleanup_expired_keys_internal(time.time())


_global_rate_limiter = InMemoryRateLimiter(requests_per_minute=60)
_l3_rate_limiter = InMemoryRateLimiter(requests_per_minute=10)


def get_global_rate_limiter() -> InMemoryRateLimiter:
    return _global_rate_limiter


def get_l3_rate_limiter() -> InMemoryRateLimiter:
    return _l3_rate_limiter


def check_l3_rate_limit(user_id: Optional[str] = None) -> tuple[bool, int, float]:
    """
    Verifies if the current user/session is permitted to execute high-cost L3 reasoning.
    Limits L3 reasoning calls to 10 requests/minute per user.
    """
    key = f"l3_user:{user_id}" if user_id else "l3_user:anonymous"
    return get_l3_rate_limiter().is_allowed(key)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI Middleware enforcing client rate limits per IP and SSO user.
    """

    EXEMPT_PATHS = {"/", "/healthz", "/health", "/readyz", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}

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

        # Safely extract client IP from trusted proxy or client host
        is_behind_lb = os.getenv("BEHIND_LOAD_BALANCER", "false").lower() in ("true", "1", "yes")
        forwarded = request.headers.get("X-Forwarded-For", "").strip()
        if forwarded:
            ips = [ip.strip()[:45] for ip in forwarded.split(",") if ip.strip()]
            if is_behind_lb:
                # With Google Cloud External HTTPS Load Balancer, X-Forwarded-For appends:
                # [client_supplied_header_ips..., real_client_ip, gfe_lb_ip]
                # ips[-1] is the Google Front End (GFE) LB IP.
                # ips[-2] is the verified originating client IP.
                # ips[0] is user-controlled and untrusted if attacker sent custom X-Forwarded-For.
                client_ip = ips[-2] if len(ips) >= 2 else ips[-1]
            else:
                # Direct Cloud Run deployment (without Load Balancer):
                # Cloud Run appends the verified client IP to the end of X-Forwarded-For (ips[-1]).
                client_ip = ips[-1] if ips else "unknown"
        elif request.client and request.client.host:
            client_ip = request.client.host[:45]
        else:
            client_ip = "unknown"

        # User authorization header hash or IP
        auth_header = request.headers.get("Authorization", "")
        key = f"ip:{client_ip[:45]}" if not auth_header else f"auth:{hash(auth_header)}"

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
