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
