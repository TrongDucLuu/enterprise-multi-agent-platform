"""
Enterprise Deployment Monthly Token Budget & Degrade Mode Manager.
Tracks aggregated LLM token usage across the deployment in Redis (or in-memory fallback).
Triggers Degrade Mode when monthly quota is exceeded:
  - L3 Deep Reasoning: Rejected/blocked.
  - L1 FAQ / L2 RAG: Allowed.
Alert deduplication: Logs ALERT exactly once per billing cycle (month).
"""
import datetime
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger("agent_core")


class DeploymentTokenBudgetTracker:
    """
    Cluster-wide token budget tracker powered by Redis with In-Memory fallback.
    Thread-safe and multi-process safe across Cloud Run instances.
    """

    def __init__(
        self,
        monthly_budget: Optional[int] = None,
        redis_client=None,
        host: Optional[str] = None,
        port: Optional[int] = None,
        db: int = 0,
        password: Optional[str] = None,
        ssl: Optional[bool] = None,
    ):
        self._monthly_budget = monthly_budget
        self._redis = redis_client
        self._host = host or os.getenv("REDIS_HOST", "localhost")
        self._port = int(port or os.getenv("REDIS_PORT", "6379"))
        self._db = int(os.getenv("REDIS_DB", str(db)))
        self._password = password or os.getenv("REDIS_AUTH_STRING", os.getenv("REDIS_PASSWORD", None)) or None
        if ssl is not None:
            self._ssl = ssl
        else:
            self._ssl = os.getenv("REDIS_USE_TLS", os.getenv("REDIS_SSL", "false")).lower() in ("true", "1", "yes")

        # In-memory accumulator fallback
        self._in_memory_usage: dict[str, int] = {}
        self._alerted_months: set[str] = set()
        self._lock = threading.Lock()

        backend = os.getenv("RATE_LIMIT_BACKEND", os.getenv("TOKEN_BUDGET_BACKEND", "memory")).lower()
        if self._redis is None and (backend == "redis" or (backend == "auto" and os.getenv("REDIS_HOST"))):
            self._init_redis()

    def _init_redis(self) -> None:
        try:
            import redis
            kwargs = {
                "host": self._host,
                "port": self._port,
                "db": self._db,
                "socket_connect_timeout": 2.0,
                "socket_timeout": 2.0,
                "decode_responses": True,
            }
            if self._password:
                kwargs["password"] = self._password
            if self._ssl:
                kwargs["ssl"] = True
            self._redis = redis.Redis(**kwargs)
            self._redis.ping()
        except Exception as e:
            logger.error("Failed to connect to Redis Token Budget Tracker (%s:%s): %s. Operating in local memory.", self._host, self._port, e)
            self._redis = None

    @property
    def monthly_budget(self) -> int:
        if self._monthly_budget is not None:
            return self._monthly_budget
        env_val = os.getenv("MONTHLY_TOKEN_BUDGET", "0").strip()
        try:
            return int(env_val)
        except ValueError:
            return 0

    @property
    def budget(self) -> int:
        return self.monthly_budget

    @classmethod
    def current_month_key(cls) -> str:
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y_%m")

    def record_token_usage(self, token_count: int, month_key: Optional[str] = None) -> int:
        """
        Increments monthly token usage by token_count.
        Checks for budget breach and logs ALERT exactly once per cycle.
        Returns the new total token count for the month.
        """
        if token_count <= 0:
            return self.get_monthly_usage(month_key)

        m_key = month_key or self.current_month_key()
        budget = self.monthly_budget
        redis_key = f"token_budget:monthly:{m_key}"
        alert_key = f"token_budget:alerted:{m_key}"

        new_total = 0
        if self._redis is not None:
            try:
                new_total = self._redis.incrby(redis_key, token_count)
                # Set TTL 60 days to auto-expire past months
                self._redis.expire(redis_key, 5184000)
            except Exception as e:
                logger.error("Redis record_token_usage error (%s). Falling back to in-memory.", e)
                with self._lock:
                    self._in_memory_usage[m_key] = self._in_memory_usage.get(m_key, 0) + token_count
                    new_total = self._in_memory_usage[m_key]
        else:
            with self._lock:
                self._in_memory_usage[m_key] = self._in_memory_usage.get(m_key, 0) + token_count
                new_total = self._in_memory_usage[m_key]

        # Check budget and trigger deduplicated ALERT
        if budget > 0 and new_total >= budget:
            self._trigger_alert_if_not_already(m_key, new_total, budget, alert_key)

        return new_total

    def _trigger_alert_if_not_already(self, month_key: str, total_tokens: int, budget: int, alert_key: str) -> None:
        """Ensures the ALERT log is emitted exactly ONCE per month."""
        should_alert = False

        if self._redis is not None:
            try:
                # setnx returns True only if key did not exist
                should_alert = bool(self._redis.set(alert_key, "1", nx=True, ex=5184000))
            except Exception:
                with self._lock:
                    if month_key not in self._alerted_months:
                        self._alerted_months.add(month_key)
                        should_alert = True
        else:
            with self._lock:
                if month_key not in self._alerted_months:
                    self._alerted_months.add(month_key)
                    should_alert = True

        if should_alert:
            logger.critical(
                "ALERT: Monthly deployment token budget exceeded for %s! Total usage: %d tokens (Budget: %d tokens). "
                "Degrade Mode is now ACTIVE: L3 reasoning is disabled; L1 FAQ / L2 RAG remain operational.",
                month_key,
                total_tokens,
                budget,
            )

    def get_monthly_usage(self, month_key: Optional[str] = None) -> int:
        m_key = month_key or self.current_month_key()
        if self._redis is not None:
            try:
                val = self._redis.get(f"token_budget:monthly:{m_key}")
                return int(val) if val else 0
            except Exception:
                pass
        with self._lock:
            return self._in_memory_usage.get(m_key, 0)

    def is_budget_exceeded(self, month_key: Optional[str] = None) -> bool:
        budget = self.monthly_budget
        if budget <= 0:
            return False
        return self.get_monthly_usage(month_key) >= budget

    def reset(self) -> None:
        with self._lock:
            self._in_memory_usage.clear()
            self._alerted_months.clear()
        if self._redis is not None:
            try:
                keys = self._redis.keys("token_budget:*")
                if keys:
                    self._redis.delete(*keys)
            except Exception:
                pass


_global_token_tracker: Optional[DeploymentTokenBudgetTracker] = None
_token_tracker_lock = threading.Lock()


def get_token_budget_tracker() -> DeploymentTokenBudgetTracker:
    global _global_token_tracker
    if _global_token_tracker is None:
        with _token_tracker_lock:
            if _global_token_tracker is None:
                _global_token_tracker = DeploymentTokenBudgetTracker()
    return _global_token_tracker


get_deployment_token_budget_tracker = get_token_budget_tracker


def record_token_usage(token_count: int, month_key: Optional[str] = None) -> int:
    return get_token_budget_tracker().record_token_usage(token_count, month_key=month_key)


def is_budget_exceeded(month_key: Optional[str] = None) -> bool:
    return get_token_budget_tracker().is_budget_exceeded(month_key=month_key)


def get_monthly_token_usage(month_key: Optional[str] = None) -> int:
    return get_token_budget_tracker().get_monthly_usage(month_key=month_key)


def reset_token_budget_tracker() -> None:
    global _global_token_tracker
    with _token_tracker_lock:
        if _global_token_tracker is not None:
            _global_token_tracker.reset()
        _global_token_tracker = None
