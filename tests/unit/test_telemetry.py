"""
Unit tests for Product Analytics & Conversation Telemetry.
"""

import pytest
from it_helpdesk_agent.app_utils.telemetry import ProductMetricsCollector
from it_helpdesk_agent.app_utils.sso_auth import SSOUser, create_dev_mock_token
from it_helpdesk_agent.fast_api_app import app
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def clean_telemetry_buffer():
    ProductMetricsCollector.clear_buffer()
    yield
    ProductMetricsCollector.clear_buffer()


def test_record_interaction_and_summary_stats():
    # Empty stats
    stats = ProductMetricsCollector.get_summary_stats()
    assert stats["total_interactions"] == 0
    assert stats["cache_hit_rate_pct"] == 0.0

    # Record L1 cache hit interaction
    ProductMetricsCollector.record_interaction(
        session_id="sess-001",
        user_id="user-123",
        domain="company.com",
        query="Cách đổi mật khẩu",
        tier_invoked="L1",
        system="GENERAL",
        cache_hit=True,
        latency_ms=45.2,
        resolution_status="RESOLVED_L1"
    )

    # Record L2 RAG interaction
    ProductMetricsCollector.record_interaction(
        session_id="sess-002",
        user_id="user-456",
        domain="company.com",
        query="Lỗi PO SAP ME21N",
        tier_invoked="L2",
        system="ERP",
        cache_hit=False,
        latency_ms=520.0,
        resolution_status="RESOLVED_L2_RAG"
    )

    stats = ProductMetricsCollector.get_summary_stats()
    assert stats["total_interactions"] == 2
    assert stats["cache_hit_count"] == 1
    assert stats["cache_hit_rate_pct"] == 50.0
    assert stats["tier_breakdown"]["L1"] == 1
    assert stats["tier_breakdown"]["L2"] == 1
    assert stats["system_breakdown"]["ERP"] == 1
    assert stats["avg_latency_ms"] == round((45.2 + 520.0) / 2, 2)


def test_analytics_summary_endpoint(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", True)

    ProductMetricsCollector.record_interaction(
        session_id="sess-100",
        user_id="admin-01",
        domain="company.com",
        query="Kiểm tra hệ thống",
        tier_invoked="L3",
        system="HRM",
        cache_hit=False,
        latency_ms=800.0,
        resolution_status="ESCALATED_L3"
    )

    user = SSOUser(
        user_id="admin-01",
        email="admin@company.com",
        roles=["admin", "employee"],
        is_authenticated=True
    )
    token = create_dev_mock_token(user)

    client = TestClient(app)
    response = client.get(
        "/api/analytics/summary",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_interactions"] == 1
    assert data["tier_breakdown"]["L3"] == 1
    assert data["system_breakdown"]["HRM"] == 1


@pytest.mark.asyncio
async def test_telemetry_live_wiring_in_agent_callbacks():
    """Verify that agent callbacks automatically record interaction metrics into telemetry."""
    from google.adk.models import LlmRequest, LlmResponse
    from google.genai import types
    from unittest.mock import MagicMock
    from it_helpdesk_agent.agent import (
        semantic_cache_before_model_callback,
        semantic_cache_after_model_callback,
    )
    from it_helpdesk_agent.app_utils.sso_auth import current_sso_user
    from it_helpdesk_agent.app_utils.semantic_cache import get_semantic_cache

    user = SSOUser(
        user_id="user-auto-01",
        email="emp@enterprise.com",
        roles=["employee"],
        hosted_domain="enterprise.com",
        is_authenticated=True
    )
    current_sso_user.set(user)

    # 1. Setup mock invocation context
    mock_cb_ctx = MagicMock()
    mock_inv_ctx = MagicMock()
    mock_inv_ctx.agent.name = "l1_selfservice_agent"
    mock_inv_ctx.session.id = "sess-live-999"

    mock_user_event = MagicMock()
    mock_user_event.author = "user"
    mock_user_part = MagicMock()
    mock_user_part.text = "Làm sao để tạo Purchase Order SAP ME21N?"
    mock_user_event.content.parts = [mock_user_part]
    mock_inv_ctx._get_events.return_value = [mock_user_event]
    mock_cb_ctx._invocation_context = mock_inv_ctx

    # 2. Simulate model response in after_model_callback
    model_response = LlmResponse(
        content=types.Content(
            role="model",
            parts=[types.Part.from_text(text="Hướng dẫn tạo PO ME21N: Bước 1...")]
        )
    )

    await semantic_cache_after_model_callback(mock_cb_ctx, model_response)

    # Check telemetry buffer
    stats = ProductMetricsCollector.get_summary_stats()
    assert stats["total_interactions"] == 1
    assert stats["cache_hit_count"] == 0
    assert stats["tier_breakdown"]["L1_SELFSERVICE_AGENT"] == 1
    assert stats["system_breakdown"]["ERP"] == 1

    # 3. Simulate subsequent cache hit in before_model_callback
    req = LlmRequest(
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text="Làm sao để tạo Purchase Order SAP ME21N?")])
        ]
    )
    hit_resp = await semantic_cache_before_model_callback(mock_cb_ctx, req)
    assert hit_resp is not None
    assert hit_resp.custom_metadata.get("cached") is True

    # Check updated stats after cache hit
    updated_stats = ProductMetricsCollector.get_summary_stats()
    assert updated_stats["total_interactions"] == 2
    assert updated_stats["cache_hit_count"] == 1
    assert updated_stats["cache_hit_rate_pct"] == 50.0


def test_telemetry_privacy_anonymization_and_redaction(monkeypatch):
    """Verify that regulated environments (Banking/Pharma) can anonymize user IDs and redact query snippets."""
    monkeypatch.setattr("it_helpdesk_agent.app_utils.telemetry.TELEMETRY_ANONYMIZE_USERS", True)
    monkeypatch.setattr("it_helpdesk_agent.app_utils.telemetry.TELEMETRY_INCLUDE_QUERY", False)

    event = ProductMetricsCollector.record_interaction(
        session_id="sess-priv-1",
        user_id="sensitive_officer_999",
        domain="bank.corp",
        query="Truy vấn số tài khoản VIP 123456789",
        tier_invoked="L2",
        system="CORE_BANKING"
    )

    assert event["user_id"].startswith("anon_")
    assert "sensitive_officer_999" not in event["user_id"]
    assert event["query_snippet"] == "[REDACTED_PRIVACY]"
    assert "123456789" not in event["query_snippet"]

