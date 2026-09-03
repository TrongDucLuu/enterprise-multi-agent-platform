"""
Unit tests for Authorize-before-Rerank and Adaptive Retrieval in BigQuery Vector Knowledge Store.

Verifies:
1. Candidate authorization happens BEFORE invoking the reranker (no unauthorized docs reach Ranking API).
2. Adaptive retrieval triggers additional query rounds when authorized results < final_k.
3. Adaptive retrieval stops when reaching final_k or hitting max adaptive_retrieval_rounds.
4. If round 1 already contains >= final_k authorized documents, subsequent rounds are skipped.
5. System config retrieval validation fails closed on invalid parameters.
"""

from unittest.mock import MagicMock, patch
import pytest
from agent_core.tools.enterprise_rag_mcp.knowledge_store import (
    BigQueryVectorKnowledgeStore,
    SecurityContext,
)
from agent_core.tools.enterprise_rag_mcp.rag_models import SearchResult
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user
from agent_core.app_utils.system_config import (
    load_system_config,
    get_retrieval_config,
    SystemConfigurationError,
)

pytestmark = pytest.mark.usefixtures("pinned_it_helpdesk_pack")


@pytest.fixture
def test_user():
    user = SSOUser(
        user_id="user-restricted-01",
        email="restricted.user@company.com",
        roles=["employee"],
        clearance_level=1,
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)


def _make_mock_row(art_id: str, title: str, roles: list[str], clearance: int = 1, distance: float = 0.2):
    row = MagicMock()
    row.id = art_id
    row.parent_doc_id = None
    row.chunk_index = 0
    row.system = "ERP"
    row.title = title
    row.content = f"Content for {title}"
    row.section_h1 = "Overview"
    row.section_h2 = None
    row.section_h3 = None
    row.section_hierarchy = None
    row.allowed_roles = roles
    row.sensitivity = "INTERNAL"
    row.clearance_level = clearance
    row.source_uri = "https://wiki.corp/doc"
    row.category = "Guides"
    row.keywords = ["help", "guide"]
    row.owner = "ops"
    row.effective_date = "2020-01-01"
    row.expiry_date = "2030-01-01"
    row.is_deleted = False
    row.distance = distance
    return row


def test_authorize_before_rerank_filters_unauthorized_docs(test_user):
    """Ensure unauthorized docs (role mismatch or high clearance) are filtered out BEFORE reranking."""
    mock_bq = MagicMock()

    store = BigQueryVectorKnowledgeStore(
        project_id="test-proj",
        dataset_id="test_ds",
        table_name="kb",
        bq_client=mock_bq,
        embedding_fn=lambda q: [0.1] * 768,
    )
    store._index_active_cache = (False, 9999999999.0)

    row_auth = _make_mock_row("DOC-AUTH-1", "Public Guide", ["employee"], clearance=1)
    row_unauth_role = _make_mock_row("DOC-UNAUTH-ROLE", "Secret Exec Memo", ["executive"], clearance=1)
    row_unauth_clearance = _make_mock_row("DOC-UNAUTH-CLEARANCE", "Top Secret", ["employee"], clearance=3)

    mock_query_job = MagicMock()
    mock_query_job.result.return_value = [row_auth, row_unauth_role, row_unauth_clearance]
    mock_bq.query.return_value = mock_query_job

    sec_ctx = SecurityContext.from_user(roles=["employee"], clearance_level=1)

    with patch("agent_core.tools.enterprise_rag_mcp.knowledge_store.get_retrieval_config") as mock_cfg, \
         patch("agent_core.tools.enterprise_rag_mcp.knowledge_store.rerank_search_results") as mock_rerank:

        mock_cfg.return_value = {
            "fraction_lists_to_search": 0.05,
            "hybrid_search_enabled": True,
            "reranker_enabled": True,
            "retrieve_k": 20,
            "final_k": 3,
            "adaptive_retrieval_rounds": 1,
        }

        mock_rerank.side_effect = lambda query, candidates, **kwargs: candidates

        results = store.search(
            query="help desk login",
            security_context=sec_ctx,
            limit=3,
        )

        assert mock_rerank.called
        rerank_call_args = mock_rerank.call_args
        candidates_passed_to_rerank = rerank_call_args.kwargs.get("candidates")
        assert len(candidates_passed_to_rerank) == 1
        assert candidates_passed_to_rerank[0].article_id == "DOC-AUTH-1"

        assert len(results) == 1
        assert results[0].article_id == "DOC-AUTH-1"


def test_adaptive_retrieval_triggers_second_round_when_needed(test_user):
    """When Round 1 doesn't yield final_k authorized docs, Round 2 triggers with larger limit."""
    mock_bq = MagicMock()

    store = BigQueryVectorKnowledgeStore(
        project_id="test-proj",
        dataset_id="test_ds",
        table_name="kb",
        bq_client=mock_bq,
        embedding_fn=lambda q: [0.1] * 768,
    )
    store._index_active_cache = (False, 9999999999.0)

    # Round 1 returns 20 rows (1 authorized + 19 unauthorized by role)
    r1_rows = [_make_mock_row("DOC-1", "Guide 1", ["employee"], clearance=1)]
    for i in range(2, 21):
        r1_rows.append(_make_mock_row(f"DOC-SECRET-{i}", f"Secret {i}", ["executive"], clearance=1))

    # Round 2 returns 40 rows (with DOC-2 and DOC-3 authorized)
    r2_rows = list(r1_rows)
    r2_rows.append(_make_mock_row("DOC-2", "Guide 2", ["employee"], clearance=1))
    r2_rows.append(_make_mock_row("DOC-3", "Guide 3", ["employee"], clearance=1))
    for i in range(23, 41):
        r2_rows.append(_make_mock_row(f"DOC-SECRET-{i}", f"Secret {i}", ["executive"], clearance=1))

    job1 = MagicMock()
    job1.result.return_value = r1_rows

    job2 = MagicMock()
    job2.result.return_value = r2_rows

    mock_bq.query.side_effect = [job1, job2]

    sec_ctx = SecurityContext.from_user(roles=["employee"], clearance_level=1)

    with patch("agent_core.tools.enterprise_rag_mcp.knowledge_store.get_retrieval_config") as mock_cfg:
        mock_cfg.return_value = {
            "fraction_lists_to_search": 0.05,
            "hybrid_search_enabled": True,
            "reranker_enabled": False,
            "retrieve_k": 20,
            "final_k": 3,
            "adaptive_retrieval_rounds": 2,
        }

        results = store.search(
            query="knowledge search",
            security_context=sec_ctx,
            limit=3,
        )

        assert mock_bq.query.call_count == 2
        assert len(results) == 3
        result_ids = [r.article_id for r in results]
        assert result_ids == ["DOC-1", "DOC-2", "DOC-3"]


def test_adaptive_retrieval_stops_at_max_rounds(test_user):
    """When authorized results remain below final_k, stops after adaptive_retrieval_rounds."""
    mock_bq = MagicMock()

    store = BigQueryVectorKnowledgeStore(
        project_id="test-proj",
        dataset_id="test_ds",
        table_name="kb",
        bq_client=mock_bq,
        embedding_fn=lambda q: [0.1] * 768,
    )
    store._index_active_cache = (False, 9999999999.0)

    # 20 rows, only 1 authorized
    r1_rows = [_make_mock_row("DOC-1", "Guide 1", ["employee"], clearance=1)]
    for i in range(2, 21):
        r1_rows.append(_make_mock_row(f"DOC-SECRET-{i}", f"Secret {i}", ["executive"], clearance=1))

    # 40 rows, still only 1 authorized
    r2_rows = list(r1_rows)
    for i in range(21, 41):
        r2_rows.append(_make_mock_row(f"DOC-SECRET-{i}", f"Secret {i}", ["executive"], clearance=1))

    job1 = MagicMock()
    job1.result.return_value = r1_rows
    job2 = MagicMock()
    job2.result.return_value = r2_rows

    mock_bq.query.side_effect = [job1, job2]

    sec_ctx = SecurityContext.from_user(roles=["employee"], clearance_level=1)

    with patch("agent_core.tools.enterprise_rag_mcp.knowledge_store.get_retrieval_config") as mock_cfg:
        mock_cfg.return_value = {
            "fraction_lists_to_search": 0.05,
            "hybrid_search_enabled": True,
            "reranker_enabled": False,
            "retrieve_k": 20,
            "final_k": 3,
            "adaptive_retrieval_rounds": 2,
        }

        results = store.search(
            query="knowledge search",
            security_context=sec_ctx,
            limit=3,
        )

        assert mock_bq.query.call_count == 2
        assert len(results) == 1
        assert results[0].article_id == "DOC-1"


def test_adaptive_retrieval_skips_second_round_when_round1_sufficient(test_user):
    """If Round 1 finds enough authorized results, no extra rounds are queried."""
    mock_bq = MagicMock()

    store = BigQueryVectorKnowledgeStore(
        project_id="test-proj",
        dataset_id="test_ds",
        table_name="kb",
        bq_client=mock_bq,
        embedding_fn=lambda q: [0.1] * 768,
    )
    store._index_active_cache = (False, 9999999999.0)

    rows = [
        _make_mock_row(f"DOC-{i}", f"Guide {i}", ["employee"], clearance=1)
        for i in range(1, 6)
    ]
    job = MagicMock()
    job.result.return_value = rows
    mock_bq.query.return_value = job

    sec_ctx = SecurityContext.from_user(roles=["employee"], clearance_level=1)

    with patch("agent_core.tools.enterprise_rag_mcp.knowledge_store.get_retrieval_config") as mock_cfg:
        mock_cfg.return_value = {
            "fraction_lists_to_search": 0.05,
            "hybrid_search_enabled": True,
            "reranker_enabled": False,
            "retrieve_k": 20,
            "final_k": 3,
            "adaptive_retrieval_rounds": 2,
        }

        results = store.search(
            query="knowledge search",
            security_context=sec_ctx,
            limit=3,
        )

        assert mock_bq.query.call_count == 1
        assert len(results) == 3


def test_retrieval_config_validation_fail_closed():
    """Verify that malformed retrieval config in systems.yaml raises SystemConfigurationError."""
    with patch("yaml.safe_load") as mock_yaml, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.is_file", return_value=True), \
         patch("builtins.open"):

        # Invalid retrieve_k (non-positive int)
        mock_yaml.return_value = {
            "systems": {"ERP": {"name": "ERP", "category": "Ops"}},
            "retrieval": {"retrieve_k": -5},
        }
        with pytest.raises(SystemConfigurationError, match="retrieve_k"):
            load_system_config(force_reload=True)

        # Invalid final_k
        mock_yaml.return_value = {
            "systems": {"ERP": {"name": "ERP", "category": "Ops"}},
            "retrieval": {"final_k": "invalid"},
        }
        with pytest.raises(SystemConfigurationError, match="final_k"):
            load_system_config(force_reload=True)

        # Invalid adaptive_retrieval_rounds
        mock_yaml.return_value = {
            "systems": {"ERP": {"name": "ERP", "category": "Ops"}},
            "retrieval": {"adaptive_retrieval_rounds": 0},
        }
        with pytest.raises(SystemConfigurationError, match="adaptive_retrieval_rounds"):
            load_system_config(force_reload=True)

        # Invalid reranker_enabled (non-boolean)
        mock_yaml.return_value = {
            "systems": {"ERP": {"name": "ERP", "category": "Ops"}},
            "retrieval": {"reranker_enabled": "yes_please"},
        }
        with pytest.raises(SystemConfigurationError, match="reranker_enabled"):
            load_system_config(force_reload=True)

    # Reload valid config to reset cache
    load_system_config(force_reload=True)
