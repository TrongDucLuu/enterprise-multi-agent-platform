import os
import time
import hashlib
import logging
import threading
from abc import ABC, abstractmethod
from typing import Optional, Tuple
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("agent_core")


class BaseRateLimiter(ABC):
    """Abstract base class for sliding window rate limiters."""

    @abstractmethod
    def is_allowed(self, key: str, max_requests: Optional[int] = None) -> Tuple[bool, int, float]:
        """
        Evaluates whether a request for `key` is allowed.
        Returns: (allowed: bool, remaining_requests: int, reset_after_seconds: float)
        """
        pass


class InMemoryRateLimiter(BaseRateLimiter):
    """
    Thread-safe in-memory Sliding Window Rate Limiter.
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

    def is_allowed(self, key: str, max_requests: Optional[int] = None) -> Tuple[bool, int, float]:
        """
        Evaluates whether a request for `key` is allowed.
        Returns: (allowed: bool, remaining_requests: int, reset_after_seconds: float)
        """
        limit = max_requests if max_requests is not None else self.requests_per_minute
        now = time.time()
        window_start = now - self._window_seconds

        with self._lock:
            self._op_count += 1
            if self._op_count >= 50 or (now - self._last_cleanup) > 60.0:
                self._cleanup_expired_keys_internal(now)
                self._last_cleanup = now
                self._op_count = 0

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


class RedisRateLimiter(BaseRateLimiter):
    """
    Cluster-wide Sliding Window Rate Limiter powered by Redis / Memorystore.
    Uses sorted sets for atomic sliding-window accounting across all Cloud Run instances.
    
    Fail-Open Architecture: If Redis is unavailable or times out, logs ERROR and
    gracefully falls back to local InMemoryRateLimiter without dropping user traffic.
    """

    def __init__(
        self,
        requests_per_minute: int = 60,
        redis_client=None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        db: int = 0,
        socket_timeout: float = 2.0,
    ):
        self.requests_per_minute = int(os.getenv("RATE_LIMIT_PER_MINUTE", requests_per_minute))
        self._window_seconds = 60.0
        self._fallback_limiter = InMemoryRateLimiter(requests_per_minute=self.requests_per_minute)
        self._redis = redis_client
        self._host = host or os.getenv("REDIS_HOST", "localhost")
        self._port = int(port or os.getenv("REDIS_PORT", "6379"))
        self._db = int(os.getenv("REDIS_DB", str(db)))
        self._socket_timeout = socket_timeout

        if self._redis is None:
            self._init_redis()

    def _init_redis(self) -> None:
        """Lazily connects to Redis with strict socket timeouts."""
        try:
            import redis
            self._redis = redis.Redis(
                host=self._host,
                port=self._port,
                db=self._db,
                socket_connect_timeout=self._socket_timeout,
                socket_timeout=self._socket_timeout,
                decode_responses=True,
            )
            # Ping test
            self._redis.ping()
            logger.info("Connected to Redis Rate Limiter at %s:%s (db=%d)", self._host, self._port, self._db)
        except Exception as e:
            logger.error("Failed to connect to Redis Rate Limiter (%s:%s): %s. Operating in Fail-Open mode.", self._host, self._port, e)
            self._redis = None

    def is_allowed(self, key: str, max_requests: Optional[int] = None) -> Tuple[bool, int, float]:
        """
        Evaluates sliding window rate limit via Redis Sorted Set.
        Fails open to in-memory fallback on any network/Redis failure.
        """
        limit = max_requests if max_requests is not None else self.requests_per_minute

        if self._redis is None:
            # Try reconnect once
            self._init_redis()
            if self._redis is None:
                return self._fallback_limiter.is_allowed(key, max_requests=limit)

        now = time.time()
        window_start = now - self._window_seconds
        redis_key = f"ratelimit:zset:{key}"

        try:
            pipe = self._redis.pipeline()
            # 1. Remove expired timestamps outside the sliding window
            pipe.zremrangebyscore(redis_key, 0, window_start)
            # 2. Count active timestamps in window
            pipe.zcard(redis_key)
            # 3. Fetch oldest timestamp in window to compute retry_after
            pipe.zrange(redis_key, 0, 0, withscores=True)
            # 4. Refresh TTL for the key
            pipe.expire(redis_key, int(self._window_seconds * 2))
            
            _, count, oldest_entries, _ = pipe.execute()

            if count >= limit:
                oldest_ts = oldest_entries[0][1] if oldest_entries else now
                reset_after = max(0.0, (oldest_ts + self._window_seconds) - now)
                return False, 0, round(reset_after, 2)

            # Record current request timestamp
            pipe2 = self._redis.pipeline()
            # Use millisecond precision timestamp as member name to ensure uniqueness
            member_id = f"{now:.6f}:{threading.get_ident()}"
            pipe2.zadd(redis_key, {member_id: now})
            pipe2.expire(redis_key, int(self._window_seconds * 2))
            pipe2.execute()

            remaining = limit - (count + 1)
            return True, max(0, remaining), 0.0

        except Exception as e:
            logger.error("RedisRateLimiter error (%s) on key %s. Falling back to local in-memory (Fail-Open).", e, key)
            return self._fallback_limiter.is_allowed(key, max_requests=limit)


# Singletons
_global_rate_limiter: Optional[BaseRateLimiter] = None
_l3_rate_limiter: Optional[BaseRateLimiter] = None
_limiter_lock = threading.Lock()


def get_global_rate_limiter() -> BaseRateLimiter:
    """Returns the configured Global Rate Limiter singleton (Redis or In-Memory)."""
    global _global_rate_limiter
    if _global_rate_limiter is None:
        with _limiter_lock:
            if _global_rate_limiter is None:
                backend = os.getenv("RATE_LIMIT_BACKEND", "memory").lower()
                rpm = int(os.getenv("RATE_LIMIT_PER_MINUTE", "60"))
                if backend == "redis" or (backend == "auto" and os.getenv("REDIS_HOST")):
                    _global_rate_limiter = RedisRateLimiter(requests_per_minute=rpm)
                else:
                    _global_rate_limiter = InMemoryRateLimiter(requests_per_minute=rpm)
    return _global_rate_limiter


def get_l3_rate_limiter() -> BaseRateLimiter:
    """Returns the configured L3 Reasoning Rate Limiter singleton."""
    global _l3_rate_limiter
    if _l3_rate_limiter is None:
        with _limiter_lock:
            if _l3_rate_limiter is None:
                backend = os.getenv("RATE_LIMIT_BACKEND", "memory").lower()
                l3_rpm = int(os.getenv("L3_RATE_LIMIT_PER_MINUTE", "10"))
                if backend == "redis" or (backend == "auto" and os.getenv("REDIS_HOST")):
                    _l3_rate_limiter = RedisRateLimiter(requests_per_minute=l3_rpm)
                else:
                    _l3_rate_limiter = InMemoryRateLimiter(requests_per_minute=l3_rpm)
    return _l3_rate_limiter


def check_l3_rate_limit(user_id: Optional[str] = None) -> Tuple[bool, int, float]:
    """
    Verifies if the current user/session is permitted to execute high-cost L3 reasoning.
    Limits L3 reasoning calls to L3_RATE_LIMIT_PER_MINUTE (default: 10 req/min).
    Delegates directly to check_l3_rate_limit_with_warning to eliminate logic duplication.
    """
    allowed, remaining, reset_after, _, _ = check_l3_rate_limit_with_warning(user_id=user_id)
    return allowed, remaining, reset_after


def check_l3_rate_limit_with_warning(
    user_id: Optional[str] = None
) -> Tuple[bool, int, float, bool, Optional[str]]:
    """
    Evaluates L3 rate limit and checks for soft warning threshold (>= 80% quota consumed).
    Returns: (allowed, remaining, reset_after, is_soft_warning, warning_message)
    """
    l3_rpm = int(os.getenv("L3_RATE_LIMIT_PER_MINUTE", "10"))
    key = f"l3_user:{user_id}" if user_id else "l3_user:anonymous"
    allowed, remaining, reset_after = get_l3_rate_limiter().is_allowed(key, max_requests=l3_rpm)

    is_soft_warning = False
    warning_message = None
    if allowed:
        used = l3_rpm - remaining
        # Trigger soft warning when at or above 80% capacity
        soft_threshold_remaining = max(1, int(l3_rpm * 0.2))
        if remaining <= soft_threshold_remaining:
            is_soft_warning = True
            warning_message = (
                f"⚠️ [L3 Quota Soft Warning] Bạn đã sử dụng {used}/{l3_rpm} lượt phân tích sâu L3 trong phút này. "
                f"Còn lại {remaining} lượt khả dụng trước khi bị giới hạn tạm thời."
            )

    return allowed, remaining, reset_after, is_soft_warning, warning_message


def reset_rate_limiters() -> None:
    """Reset singletons for testing purposes."""
    global _global_rate_limiter, _l3_rate_limiter
    with _limiter_lock:
        _global_rate_limiter = None
        _l3_rate_limiter = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI Middleware enforcing client rate limits per IP and SSO user.
    """

    EXEMPT_PATHS = {"/", "/healthz", "/health", "/readyz", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}

    def __init__(self, app, limiter: Optional[BaseRateLimiter] = None):
        super().__init__(app)
        self.limiter = limiter

    def _get_limiter(self) -> BaseRateLimiter:
        return self.limiter or get_global_rate_limiter()

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
                client_ip = ips[-2] if len(ips) >= 2 else ips[-1]
            else:
                client_ip = ips[-1] if ips else "unknown"
        elif request.client and request.client.host:
            client_ip = request.client.host[:45]
        else:
            client_ip = "unknown"

        # Determine client rate-limiting key:
        # 1. Authenticated User: f"user:{sha256(user_id)}"
        # 2. Unauthenticated / Invalid Token: f"ip:{client_ip}"
        user_id = None
        try:
            from agent_core.app_utils.sso_auth import current_sso_user
            current_user = current_sso_user.get()
            if current_user and getattr(current_user, "is_authenticated", False):
                user_id = getattr(current_user, "user_id", None)
        except Exception:
            pass

        auth_header = request.headers.get("Authorization", "").strip()
        if not user_id and auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
            try:
                from agent_core.app_utils.sso_auth import verify_sso_token
                user = verify_sso_token(token)
                if user and getattr(user, "is_authenticated", False):
                    user_id = user.user_id
                    request.state.verified_sso_user = user
            except Exception as e:
                user_id = None
                request.state.sso_auth_error = e

        if user_id:
            user_hash = hashlib.sha256(str(user_id).encode("utf-8")).hexdigest()[:32]
            key = f"user:{user_hash}"
        else:
            ip_clean = client_ip[:45].strip()
            key = f"ip:{ip_clean}"

        allowed, remaining, retry_after = self._get_limiter().is_allowed(key)
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
