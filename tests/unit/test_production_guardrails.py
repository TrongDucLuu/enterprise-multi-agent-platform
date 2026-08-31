"""
Unit Tests for Production Guardrails & Resilience Mechanisms
============================================================
Tests P0, P1, P2 backlog requirements:
1. P0.1: Fail-Closed Semantic Cache in Production (bypass when Vertex AI embedding unavailable/failing)
2. P0.2: Mandatory L3 Output Guardrails (confidence_level, requires_human_review, disclaimer)
3. P2.6: Redis Circuit Breaker & REDIS_CIRCUIT_BREAKER_ALERT after >= 10 consecutive failures
4. P2.7: L3 Rate Limiting Soft Warning at >= 80% quota utilization
"""

import os
import time
import pytest
import logging
from unittest.mock import MagicMock, patch

from google.adk.models import LlmResponse

from it_helpdesk_agent.app_utils.semantic_cache import (
    InMemorySemanticCache,
    RedisSemanticCache,
    is_production_mode,
)
from it_helpdesk_agent.app_utils.rate_limiter import (
    InMemoryRateLimiter,
    RedisRateLimiter,
    check_l3_rate_limit_with_warning,
    reset_rate_limiters,
)
from it_helpdesk_agent.tools.log_analyzer import analyze_system_logs_for_rca
from it_helpdesk_agent.tools.compliance_tool import review_it_contract_sla
from it_helpdesk_agent.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture(autouse=True)
def authorized_admin_user():
    """Sets an authorized IT Admin & Compliance Officer user context."""
    user = SSOUser(
        user_id="lead-admin-01",
        email="admin@company.com",
        roles=["employee", "it_admin", "sys_admin", "compliance_officer", "legal_counsel"],
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)


# ==============================================================================
# 1. P0.1 — FAIL-CLOSED SEMANTIC CACHE IN PRODUCTION
# ==============================================================================

class TestSemanticCacheFailClosedInProduction:

    def test_is_production_mode_detection(self, monkeypatch):
        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.delenv("ENV", raising=False)
        monkeypatch.delenv("K_SERVICE", raising=False)
        assert is_production_mode() is False

        monkeypatch.setenv("ENVIRONMENT", "production")
        assert is_production_mode() is True

        monkeypatch.setenv("ENVIRONMENT", "prod")
        assert is_production_mode() is True

        monkeypatch.delenv("ENVIRONMENT", raising=False)
        monkeypatch.setenv("K_SERVICE", "it-helpdesk-agent-srv")
        assert is_production_mode() is True

    def test_in_memory_cache_fail_closed_in_prod_when_vertex_disabled(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("USE_VERTEX_EMBEDDING", "false")
        monkeypatch.delenv("K_SERVICE", raising=False)

        cache = InMemorySemanticCache()
        # In production mode without Vertex AI, embedding returns None
        assert cache._generate_embedding("hướng dẫn đổi mật khẩu") is None

        # set() should safely skip writing
        result_set = cache.set(query="hướng dẫn đổi mật khẩu", response="Bước 1...")
        assert result_set is None

        # get() should safely bypass and return None
        result_get = cache.get(query="hướng dẫn đổi mật khẩu")
        assert result_get is None

    def test_redis_cache_fail_closed_in_prod_when_vertex_embedding_fails(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("USE_VERTEX_EMBEDDING", "true")

        mock_redis = MagicMock()
        cache = RedisSemanticCache(redis_client=mock_redis)

        # Mock Vertex AI embedding error
        with patch("vertexai.language_models.TextEmbeddingModel.from_pretrained", side_effect=RuntimeError("Vertex Quota Exceeded")):
            assert cache._generate_embedding("hướng dẫn kết nối wifi") is None

            # get() should return None (bypass) without making invalid Redis query
            assert cache.get("hướng dẫn kết nối wifi") is None

            # set() should return None without polluting Redis
            assert cache.set("hướng dẫn kết nối wifi", "Bước 1...") is None


# ==============================================================================
# 2. P0.2 — MANDATORY L3 OUTPUT GUARDRAILS (RCA & SLA REVIEW)
# ==============================================================================

class TestL3MandatoryOutputGuardrails:

    def test_rca_output_contains_confidence_level_and_disclaimer(self):
        log_sample = """
        2026-08-31 10:00:01 [FATAL] Database connection pool exhausted. Max connections: 100 reached.
        2026-08-31 10:00:02 [ERROR] NullPointerException at OrderService.java:142
        2026-08-31 10:00:03 [ERROR] Transaction rollback failed.
        """
        result = analyze_system_logs_for_rca(
            raw_logs=log_sample,
            system_name="ERP",
            incident_description="Hệ thống đơn hàng ngừng tiếp nhận giao dịch"
        )

        assert result["status"] == "success"
        assert result["system"] == "ERP"
        assert result["confidence_level"] == "HIGH"
        assert result["requires_human_review"] is True
        assert "disclaimer" in result
        assert "KHÔNG phải là kết luận điều tra sự cố chính thức" in result["disclaimer"]
        assert "xác minh và phê duyệt" in result["disclaimer"]

    def test_rca_output_low_confidence_when_no_errors(self):
        clean_logs = """
        2026-08-31 10:00:01 [INFO] Service started successfully.
        2026-08-31 10:00:02 [INFO] Health check passed.
        """
        result = analyze_system_logs_for_rca(
            raw_logs=clean_logs,
            system_name="CRM"
        )
        assert result["status"] == "success"
        assert result["confidence_level"] == "LOW"
        assert result["requires_human_review"] is True

    def test_sla_review_contains_confidence_level_and_legal_disclaimer(self):
        contract_text = """
        Nhà cung cấp cam kết Uptime đạt tối thiểu 99.95% mỗi tháng theo lịch 24/7.
        Thời gian khắc phục sự cố MTTR tối đa 4 giờ đối với sự cố mức độ Critical.
        Trường hợp không đạt cam kết, áp dụng mức bồi thường phạt 10% Service Credits.
        """
        result = review_it_contract_sla(
            contract_text=contract_text,
            vendor_name="Cloud SaaS Provider"
        )

        assert result["status"] == "success"
        assert result["vendor"] == "Cloud SaaS Provider"
        assert result["confidence_level"] == "HIGH"
        assert result["requires_human_review"] is True
        assert "disclaimer" in result
        assert "KHÔNG cấu thành ý kiến tư vấn pháp lý" in result["disclaimer"]
        assert "Bộ phận Pháp chế" in result["disclaimer"]


# ==============================================================================
# 3. P2.6 — REDIS CIRCUIT BREAKER & ALERTING
# ==============================================================================

class TestRedisCircuitBreakerAndAlerting:

    def test_redis_circuit_breaker_trips_after_10_consecutive_failures(self, caplog):
        mock_redis = MagicMock()
        mock_redis.smembers.side_effect = ConnectionError("Redis cluster unreachable")

        cache = RedisSemanticCache(redis_client=mock_redis)
        cache._failure_threshold = 10

        with caplog.at_level(logging.CRITICAL):
            # Simulate 10 consecutive failures
            for i in range(10):
                res = cache.get("test query")
                assert res is None

            # Verify Circuit Breaker Tripped and Alert Emitted
            assert cache._circuit_breaker_tripped is True
            assert cache._consecutive_redis_failures >= 10
            assert any("REDIS_CIRCUIT_BREAKER_ALERT" in record.message for record in caplog.records)

        # 11th call should be fast-bypassed by circuit breaker without calling redis
        mock_redis.smembers.reset_mock()
        assert cache.get("test query 2") is None
        mock_redis.smembers.assert_not_called()

    def test_redis_circuit_breaker_resets_upon_recovery(self):
        mock_redis = MagicMock()
        cache = RedisSemanticCache(redis_client=mock_redis)
        cache._consecutive_redis_failures = 12
        cache._circuit_breaker_tripped = True

        # Simulate recovery
        cache._record_redis_success()
        assert cache._consecutive_redis_failures == 0
        assert cache._circuit_breaker_tripped is False

    def test_redis_circuit_breaker_half_open_auto_recovery_flow(self, monkeypatch):
        mock_redis = MagicMock()
        mock_redis.smembers.side_effect = ConnectionError("Redis down")

        cache = RedisSemanticCache(redis_client=mock_redis, cooldown_seconds=10.0)
        cache._failure_threshold = 3

        # 1. Cause 3 failures to trip circuit breaker into OPEN state
        for _ in range(3):
            assert cache.get("test") is None

        assert cache._circuit_breaker_tripped is True
        trip_time = cache._last_failure_time

        # 2. While cooldown is active (<10s), get() must NOT touch Redis (fast bypass)
        mock_redis.smembers.reset_mock()
        assert cache.get("test") is None
        mock_redis.smembers.assert_not_called()

        # 3. Simulate cooldown expiration (cooldown >= 10s) -> HALF_OPEN state allows a probe
        cache._last_failure_time = time.time() - 15.0  # 15s ago
        # Simulate Redis back online
        mock_redis.smembers.side_effect = None
        mock_redis.smembers.return_value = set()

        # Probe request
        assert cache.get("test") is None
        mock_redis.smembers.assert_called_once()

        # Circuit breaker should now be CLOSED and healthy
        assert cache._circuit_breaker_tripped is False
        assert cache._consecutive_redis_failures == 0

    def test_redis_circuit_breaker_probe_failure_remains_open(self):
        mock_redis = MagicMock()
        mock_redis.smembers.side_effect = ConnectionError("Redis down")

        cache = RedisSemanticCache(redis_client=mock_redis, cooldown_seconds=10.0)
        cache._consecutive_redis_failures = 5
        cache._circuit_breaker_tripped = True
        cache._last_failure_time = time.time() - 20.0  # Cooldown elapsed

        # Probe fails
        assert cache.get("test") is None
        assert cache._circuit_breaker_tripped is True
        # Timestamp updated to now
        assert cache._last_failure_time > time.time() - 5.0


# ==============================================================================
# 4. P2.7 — L3 RATE LIMIT SOFT WARNING AT 80% QUOTA & DELIVERED TO USER
# ==============================================================================

class TestL3RateLimitSoftWarning:

    def test_l3_rate_limit_soft_warning_at_80_percent_quota(self, monkeypatch):
        monkeypatch.setenv("L3_RATE_LIMIT_PER_MINUTE", "10")
        monkeypatch.setenv("RATE_LIMITER_BACKEND", "memory")
        reset_rate_limiters()

        user_id = "user_test_80pct"

        # Calls 1 to 7: Allowed, remaining >= 3, no soft warning
        for i in range(1, 8):
            allowed, rem, retry_after, is_soft, msg = check_l3_rate_limit_with_warning(user_id)
            assert allowed is True
            assert is_soft is False
            assert msg is None

        # Call 8: 8/10 used -> 2 remaining (80% used) -> Trigger soft warning
        allowed, rem, retry_after, is_soft, msg = check_l3_rate_limit_with_warning(user_id)
        assert allowed is True
        assert is_soft is True
        assert rem == 2
        assert "L3 Quota Soft Warning" in msg
        assert "8/10" in msg

        # Call 9: 9/10 used -> 1 remaining -> Trigger soft warning
        allowed, rem, retry_after, is_soft, msg = check_l3_rate_limit_with_warning(user_id)
        assert allowed is True
        assert is_soft is True
        assert rem == 1

        # Call 10: 10/10 used -> 0 remaining -> Trigger soft warning
        allowed, rem, retry_after, is_soft, msg = check_l3_rate_limit_with_warning(user_id)
        assert allowed is True
        assert is_soft is True
        assert rem == 0

        # Call 11: Exceeded -> blocked
        allowed, rem, retry_after, is_soft, msg = check_l3_rate_limit_with_warning(user_id)
        assert allowed is False

    @pytest.mark.asyncio
    async def test_l3_soft_warning_attached_to_user_response(self, monkeypatch):
        """
        Verifies that when an L3 request hits >= 80% quota, the soft warning message
        is prepended to the actual response text returned to the user and in custom_metadata.
        """
        import asyncio
        from google.genai import types
        from it_helpdesk_agent.agent import (
            semantic_cache_before_model_callback,
            semantic_cache_after_model_callback,
            _current_l3_soft_warning
        )
        from it_helpdesk_agent.app_utils.sso_auth import current_sso_user, SSOUser

        monkeypatch.setenv("L3_RATE_LIMIT_PER_MINUTE", "10")
        monkeypatch.setenv("RATE_LIMITER_BACKEND", "memory")
        reset_rate_limiters()

        user = SSOUser(
            user_id="user_quota_warn",
            email="warn@company.com",
            name="Warn User",
            roles=["Employee"]
        )
        current_sso_user.set(user)

        # Consume 7 requests so next call is 8th (80% quota)
        for _ in range(7):
            check_l3_rate_limit_with_warning(user.user_id)

        # Mock callback context for L3 Agent
        mock_agent = MagicMock()
        mock_agent.name = "l3_deep_diagnostics_agent"
        mock_inv_ctx = MagicMock()
        mock_inv_ctx.agent = mock_agent
        mock_inv_ctx.session.id = "sess_l3_warn"
        mock_inv_ctx.session.events = []
        mock_cb_ctx = MagicMock()
        mock_cb_ctx._invocation_context = mock_inv_ctx

        mock_req = MagicMock()
        mock_req.contents = [
            types.Content(role="user", parts=[types.Part.from_text(text="Phân tích lỗi OOM")])
        ]

        # 1. Run before_model_callback -> should allow request and set soft warning contextvar
        resp_before = await semantic_cache_before_model_callback(mock_cb_ctx, mock_req)
        assert resp_before is None  # Allowed to proceed to model
        assert _current_l3_soft_warning.get() is not None
        assert "L3 Quota Soft Warning" in _current_l3_soft_warning.get()

        # 2. Simulate model response
        original_llm_resp = LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Đây là kết quả phân tích Root Cause OOM.")]
            ),
            custom_metadata={}
        )

        # 3. Run after_model_callback -> should enrich response with soft warning
        final_resp = await semantic_cache_after_model_callback(mock_cb_ctx, original_llm_resp)

        assert final_resp is not None
        final_text = final_resp.content.parts[0].text
        assert "⚠️ [L3 Quota Soft Warning]" in final_text
        assert "Đây là kết quả phân tích Root Cause OOM." in final_text
        assert final_resp.custom_metadata.get("soft_warning") is not None
