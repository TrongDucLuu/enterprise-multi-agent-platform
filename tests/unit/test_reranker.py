import pytest
from unittest.mock import MagicMock, patch
from it_helpdesk_agent.app_utils.reranker import rerank_search_results, DEFAULT_RANKER_MODEL
from it_helpdesk_agent.tools.enterprise_rag_mcp.rag_models import SearchResult


@pytest.fixture
def sample_candidates():
    return [
        SearchResult(
            article_id="ERP-001",
            system="ERP",
            title="SAP ME21N Guide",
            snippet="ME21N Purchase Order creation",
            relevance_score=0.85,
        ),
        SearchResult(
            article_id="HRM-001",
            system="HRM",
            title="BambooHR Timesheet",
            snippet="Timesheet submission and approval",
            relevance_score=0.75,
        ),
        SearchResult(
            article_id="CRM-001",
            system="CRM",
            title="Salesforce Territory",
            snippet="Territory assignment rules",
            relevance_score=0.65,
        ),
    ]


def test_reranker_disabled_by_default(sample_candidates, monkeypatch):
    monkeypatch.setenv("USE_VERTEX_RERANKER", "false")
    results = rerank_search_results(
        query="lỗi tạo đơn mua hàng SAP",
        candidates=sample_candidates,
        top_n=2,
    )
    assert len(results) == 2
    assert results[0].article_id == "ERP-001"
    assert results[1].article_id == "HRM-001"


def test_reranker_success_reorders_and_normalizes(sample_candidates, monkeypatch):
    monkeypatch.setenv("USE_VERTEX_RERANKER", "true")

    mock_client = MagicMock()
    
    # Mock Vertex AI Ranking response placing HRM first with higher relevance
    mock_record1 = MagicMock(id="HRM-001", score=0.96)
    mock_record2 = MagicMock(id="ERP-001", score=0.88)
    mock_response = MagicMock(records=[mock_record1, mock_record2])
    mock_client.rank.return_value = mock_response

    with patch("google.cloud.discoveryengine_v1.RankServiceClient", return_value=mock_client):
        results = rerank_search_results(
            query="timesheet submission issue",
            candidates=sample_candidates,
            top_n=2,
            project_id="test-project",
        )

    assert len(results) == 2
    assert results[0].article_id == "HRM-001"
    assert results[0].score == 0.96
    assert results[1].article_id == "ERP-001"
    assert results[1].score == 0.88
    mock_client.rank.assert_called_once()


def test_reranker_graceful_fallback_on_api_error(sample_candidates, monkeypatch):
    monkeypatch.setenv("USE_VERTEX_RERANKER", "true")

    mock_client = MagicMock()
    mock_client.rank.side_effect = RuntimeError("Vertex AI Service Quota Exceeded")

    with patch("google.cloud.discoveryengine_v1.RankServiceClient", return_value=mock_client):
        results = rerank_search_results(
            query="test query",
            candidates=sample_candidates,
            top_n=3,
        )

    # Should gracefully return original candidates without raising exception
    assert len(results) == 3
    assert results[0].article_id == "ERP-001"
    assert results[1].article_id == "HRM-001"
    assert results[2].article_id == "CRM-001"
