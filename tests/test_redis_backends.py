import time
import pytest
import fakeredis
from it_helpdesk_agent.app_utils.rate_limiter import (
    RedisRateLimiter,
    InMemoryRateLimiter,
    get_global_rate_limiter,
    reset_rate_limiters,
)
from it_helpdesk_agent.app_utils.semantic_cache import (
    RedisSemanticCache,
    InMemorySemanticCache,
    SemanticCacheEntry,
    reset_semantic_cache,
)


class TestRedisRateLimiter:
    """Unit tests verifying Redis-backed Rate Limiter and Fail-Open behavior."""

    def test_redis_sliding_window_basic_allow_and_block(self):
        fake_server = fakeredis.FakeServer()
        r1 = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
        limiter = RedisRateLimiter(requests_per_minute=3, redis_client=r1)

        # 1st request -> Allowed
        allowed, rem, reset_after = limiter.is_allowed("user_123")
        assert allowed is True
        assert rem == 2
        assert reset_after == 0.0

        # 2nd request -> Allowed
        allowed, rem, _ = limiter.is_allowed("user_123")
        assert allowed is True
        assert rem == 1

        # 3rd request -> Allowed
        allowed, rem, _ = limiter.is_allowed("user_123")
        assert allowed is True
        assert rem == 0

        # 4th request -> Blocked (Exceeded 3 req/min)
        allowed, rem, reset_after = limiter.is_allowed("user_123")
        assert allowed is False
        assert rem == 0
        assert reset_after > 0.0

    def test_redis_multi_instance_cluster_sharing(self):
        """Simulates 2 Cloud Run instances sharing state via the same Redis instance."""
        fake_server = fakeredis.FakeServer()
        # Instance A connection
        r_inst_a = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
        limiter_a = RedisRateLimiter(requests_per_minute=2, redis_client=r_inst_a)

        # Instance B connection
        r_inst_b = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
        limiter_b = RedisRateLimiter(requests_per_minute=2, redis_client=r_inst_b)

        # Request 1 hits Instance A -> OK
        allowed_a, rem_a, _ = limiter_a.is_allowed("shared_user")
        assert allowed_a is True
        assert rem_a == 1

        # Request 2 hits Instance B -> OK
        allowed_b, rem_b, _ = limiter_b.is_allowed("shared_user")
        assert allowed_b is True
        assert rem_b == 0

        # Request 3 hits Instance A -> Blocked by cluster quota
        allowed_a3, rem_a3, reset_a3 = limiter_a.is_allowed("shared_user")
        assert allowed_a3 is False
        assert rem_a3 == 0

    def test_redis_fail_open_on_connection_error(self, caplog):
        """
        CRITICAL TEST: Verifies that if Redis raises an error or disconnects,
        the rate limiter fails open to local in-memory fallback without dropping traffic.
        """
        class BrokenRedis:
            def pipeline(self):
                raise ConnectionError("Redis cluster unreachable or down")

        broken_limiter = RedisRateLimiter(requests_per_minute=5, redis_client=BrokenRedis())

        # Request should succeed via fail-open fallback
        allowed, rem, reset_after = broken_limiter.is_allowed("failopen_user")
        assert allowed is True
        assert rem == 4

        # Verify ERROR log was recorded
        assert any("Fail-Open" in record.message or "error" in record.message.lower() for record in caplog.records)


class TestRedisSemanticCache:
    """Unit tests verifying Redis-backed Semantic Cache and Soft Fail-Closed behavior."""

    def test_redis_semantic_cache_hit_and_user_isolation(self):
        fake_server = fakeredis.FakeServer()
        r1 = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
        cache = RedisSemanticCache(redis_client=r1, similarity_threshold=0.85)

        # User A caches private resolution
        cache.set(
            query="Hướng dẫn cài đặt VPN FortiClient trên macOS",
            response="Tải file DMG và cấp quyền System Extension.",
            user_id="user_alice",
            is_public=False,
            tier="L1"
        )

        # Alice asks identical/similar question -> Cache Hit
        res_alice = cache.get("Hướng dẫn cài đặt VPN FortiClient trên macOS công ty", user_id="user_alice")
        assert res_alice is not None
        assert res_alice["status"] == "cache_hit"
        assert "System Extension" in res_alice["response"]

        # Bob asks the same question -> Cache Miss (User Isolation enforced)
        res_bob = cache.get("Hướng dẫn cài đặt VPN FortiClient trên macOS công ty", user_id="user_bob")
        assert res_bob is None

        # Public cache entry
        cache.set(
            query="Giờ làm việc trực tiếp của IT Helpdesk",
            response="Từ 8:00 đến 17:30 thứ 2 đến thứ 6",
            user_id="it_admin",
            is_public=True,
            tier="L1"
        )

        # Bob asks public question -> Cache Hit
        res_bob_pub = cache.get("Giờ làm việc trực tiếp của IT Helpdesk", user_id="user_bob")
        assert res_bob_pub is not None
        assert res_bob_pub["status"] == "cache_hit"
        assert "8:00 đến 17:30" in res_bob_pub["response"]

    def test_redis_semantic_cache_multi_instance_sharing(self):
        """Simulates cache write on Instance A immediately available on Instance B."""
        fake_server = fakeredis.FakeServer()
        r_a = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
        cache_a = RedisSemanticCache(redis_client=r_a, similarity_threshold=0.85)

        r_b = fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
        cache_b = RedisSemanticCache(redis_client=r_b, similarity_threshold=0.85)

        cache_a.set(
            query="Reset mật khẩu SAP ERP",
            response="Truy cập https://selfservice.erp.corp.com/reset",
            is_public=True,
            tier="L1"
        )

        # Instance B reads the cached query
        res_b = cache_b.get("Reset mật khẩu SAP ERP")
        assert res_b is not None
        assert res_b["status"] == "cache_hit"
        assert "selfservice.erp.corp.com" in res_b["response"]

    def test_redis_semantic_cache_soft_fail_closed(self, caplog):
        """
        CRITICAL TEST: Verifies that if Redis encounters an error,
        get() returns None (Cache Miss) and set() returns None without throwing exceptions.
        """
        class BrokenRedisCache:
            def smembers(self, key):
                raise TimeoutError("Redis socket timeout 2000ms exceeded")
            def pipeline(self):
                raise TimeoutError("Redis pipeline timeout")

        broken_cache = RedisSemanticCache(redis_client=BrokenRedisCache())

        # get() should gracefully return None
        result = broken_cache.get("Lỗi mạng LAN", user_id="user_123")
        assert result is None

        # set() should gracefully return None
        set_result = broken_cache.set(
            query="Lỗi mạng LAN",
            response="Kiểm tra dây cáp",
            user_id="user_123"
        )
        assert set_result is None

        # Verify warning log was recorded
        assert any("warning" in record.levelname.lower() or "fail-closed" in record.message.lower() for record in caplog.records)
