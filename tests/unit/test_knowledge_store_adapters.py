import os
import logging
from unittest.mock import MagicMock, patch
import pytest

# Pins it-helpdesk pack because tests query and assert IT Helpdesk sample articles, codes, and documents
pytestmark = pytest.mark.usefixtures("pinned_it_helpdesk_pack")

from agent_core.tools.enterprise_rag_mcp.knowledge_store import (
    BaseKnowledgeStore,
    InMemoryKnowledgeStore,
    BigQueryVectorKnowledgeStore,
    VertexAISearchKnowledgeStore,
    get_knowledge_store,
    KnowledgeArticle,
    KnowledgeStoreUnavailableError,
    SecurityContext,
    normalize_similarity,
)
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture
def test_sec_ctx():
    return SecurityContext.from_user(roles=["employee", "it_admin", "support_agent"], clearance_level=3)


@pytest.fixture(autouse=True)
def default_adapter_sso_user():
    user = SSOUser(
        user_id="test-employee-01",
        email="test.employee@company.com",
        roles=["employee", "it_admin", "support_agent"],
        clearance_level=3,
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)


def test_in_memory_knowledge_store_search_and_get(test_sec_ctx):
    store = InMemoryKnowledgeStore()
    assert isinstance(store, BaseKnowledgeStore)

    # Search ERP
    results = store.search("Purchase Order phân quyền", security_context=test_sec_ctx, system="ERP", limit=2)
    assert len(results) > 0
    assert results[0].system == "ERP"
    assert "ERP-KB-001" == results[0].article_id

    # Get by ID
    article = store.get_article_by_id("ERP-KB-001", security_context=test_sec_ctx)
    assert article is not None
    assert "SAP/Oracle" in article.title

    # Not found
    assert store.get_article_by_id("NON-EXISTENT", security_context=test_sec_ctx) is None


def test_bigquery_vector_store_with_mock_client(test_sec_ctx):
    mock_bq = MagicMock()
    
    # Using SimpleNamespace or explicit object to prevent MagicMock dynamic attribute creation
    from types import SimpleNamespace
    mock_row = SimpleNamespace(
        id="ERP-KB-001",
        parent_doc_id=None,
        chunk_index=None,
        system="ERP",
        title="Khắc phục lỗi PO",
        category="Procurement",
        content="Nội dung chi tiết về phân quyền Purchase Order...",
        section_h1="ERP Guide",
        section_h2="Procurement",
        section_h3="PO Authorization",
        section_hierarchy=None,
        allowed_roles=[],
        sensitivity="INTERNAL",
        source_uri="docs/erp_po_manual.md",
        owner="erp-team@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
        keywords=["po", "sap"],
        distance=0.15,
        hybrid_score=0.85,
        clearance_level=1,
        index_status="ACTIVE",
        coverage_percentage=100.0,
    )

    mock_query_job = MagicMock()
    mock_query_job.result.return_value = [mock_row]
    mock_bq.query.return_value = mock_query_job

    def mock_embed(text: str) -> list[float]:
        return [0.1] * 64

    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=mock_embed
    )

    # 1. Test search with system="ERP" (Pre-filter subquery)
    results = store.search("Lỗi tạo PO", security_context=test_sec_ctx, system="ERP", limit=1)
    assert len(results) == 1
    assert results[0].article_id == "ERP-KB-001"
    assert results[0].relevance_score == 0.925
    assert results[0].section_hierarchy.h1 == "ERP Guide"
    assert results[0].section_hierarchy.h2 == "Procurement"
    assert results[0].context_path == "ERP Guide > Procurement > PO Authorization"
    assert mock_bq.query.called

    sql_arg = mock_bq.query.call_args[0][0]
    # Verify pre-filtering subquery in VECTOR_SEARCH first argument
    assert "FROM VECTOR_SEARCH(" in sql_arg
    assert "WHERE system = @system_param" in sql_arg
    assert "fraction_lists_to_search" in sql_arg

    # 🔴 P0 Requirement: Verify that search() SQL generated on read path contains ALL governance conditions with @today and scalar clearance
    assert "is_deleted IS NOT TRUE" in sql_arg
    assert "expiry_date IS NULL OR expiry_date >= @today" in sql_arg
    assert "effective_date IS NULL OR effective_date <= @today" in sql_arg
    assert "clearance_level IS NULL OR clearance_level <= @user_clearance" in sql_arg

    # 2. Test search with system="HRM"
    store.search("Chấm công", security_context=test_sec_ctx, system="HRM", limit=3)
    sql_arg_hrm = mock_bq.query.call_args[0][0]
    assert "WHERE system = @system_param" in sql_arg_hrm
    assert "is_deleted IS NOT TRUE" in sql_arg_hrm
    assert "expiry_date IS NULL OR expiry_date >= @today" in sql_arg_hrm
    assert "effective_date IS NULL OR effective_date <= @today" in sql_arg_hrm
    assert "clearance_level IS NULL OR clearance_level <= @user_clearance" in sql_arg_hrm

    # 3. Test search with system="ALL" and RBAC allowed_systems (Pre-filter with allowed systems)
    store.search("Reset password", security_context=test_sec_ctx, system="ALL", limit=5, allowed_systems=["ERP", "HRM"])
    sql_arg_all = mock_bq.query.call_args[0][0]
    assert "WHERE system IN UNNEST(@allowed_systems_param)" in sql_arg_all
    assert "is_deleted IS NOT TRUE" in sql_arg_all
    assert "expiry_date IS NULL OR expiry_date >= @today" in sql_arg_all
    assert "effective_date IS NULL OR effective_date <= @today" in sql_arg_all
    assert "clearance_level IS NULL OR clearance_level <= @user_clearance" in sql_arg_all

    # 4. Test Index DDL contains STORING clause and lexical_search_columns
    mock_bq.reset_mock()
    from scripts.ingest_knowledge_base import ensure_vector_index
    ensure_vector_index(mock_bq, project_id="test-project", dataset_id="test_kb", table_name="articles")
    ddl_call = mock_bq.query.call_args[0][0]
    import re
    stored_cols = set(re.search(r"STORING \(([^)]*)\)", ddl_call).group(1).replace(" ", "").split(","))
    assert {"system", "is_deleted", "effective_date", "expiry_date", "keywords", "allowed_roles", "sensitivity", "clearance_level"} <= stored_cols
    assert "lexical_search_columns=['title', 'content', 'keywords']" in ddl_call


def test_mutation_bigquery_search_omitting_base_filters_fails(monkeypatch, test_sec_ctx):
    """
    🔴 P0 MUTATION TEST:
    If base_filters is stripped from BigQueryVectorKnowledgeStore.search() SQL generation
    (e.g., base_filters = "TRUE"), the SQL assertion must FAIL (RED).
    """
    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = []
    mock_bq.query.return_value = mock_query_job

    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64
    )

    # Perform normal search
    store.search("Tìm tài liệu M_BEST_EKO", security_context=test_sec_ctx, system="ERP", limit=2)
    generated_sql = mock_bq.query.call_args[0][0]

    # Verify that the correct SQL passes
    assert "is_deleted IS NOT TRUE" in generated_sql
    assert "expiry_date IS NULL OR expiry_date >= @today" in generated_sql
    assert "effective_date IS NULL OR effective_date <= @today" in generated_sql
    assert "clearance_level IS NULL OR clearance_level <= @user_clearance" in generated_sql

    # Simulate mutated SQL where governance filters are replaced with "TRUE"
    mutated_sql = generated_sql.replace(
        "(is_deleted IS NOT TRUE) AND (expiry_date IS NULL OR expiry_date >= @today) AND (effective_date IS NULL OR effective_date <= @today) AND (clearance_level IS NULL OR clearance_level <= @user_clearance)",
        "TRUE"
    )

    # Assert that the mutated SQL fails the governance assertion
    with pytest.raises(AssertionError):
        assert "is_deleted IS NOT TRUE" in mutated_sql
        assert "expiry_date IS NULL OR expiry_date >= @today" in mutated_sql
        assert "effective_date IS NULL OR effective_date <= @today" in mutated_sql
        assert "clearance_level IS NULL OR clearance_level <= @user_clearance" in mutated_sql


def test_bigquery_hybrid_search_sql_generation_and_behavior(monkeypatch, test_sec_ctx):
    """
    🔴 P0 NATIVE HYBRID SEARCH TEST:
    Verifies that when hybrid_search_enabled=True, BigQueryVectorKnowledgeStore generates
    native BigQuery VECTOR_SEARCH (Form 1 single query) with:
      - query_value => @query_vector
      - lexical_search_columns => ['title', 'content', 'keywords']
      - lexical_search_query_value => @query_text
    And when hybrid_search_enabled=False, generates pure vector SQL with query_value => @query_vector
    without lexical_search_columns.
    Also verifies absence of manual LIKE CONCAT / re.split tokenizer noise and incorrect parameters (mode, query_text_column).
    """
    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = []
    mock_bq.query.return_value = mock_query_job

    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64
    )

    # 1. Test with hybrid_search_enabled = True
    monkeypatch.setattr(
        "agent_core.tools.enterprise_rag_mcp.knowledge_store.get_retrieval_config",
        lambda: {"fraction_lists_to_search": 0.05, "hybrid_search_enabled": True}
    )

    query_str = "Lỗi phân quyền M_BEST_EKO khi tạo ME21N"
    store.search(query_str, security_context=test_sec_ctx, system="ERP", limit=3)
    sql_hybrid = mock_bq.query.call_args[0][0]
    job_config_hybrid = mock_bq.query.call_args[1]["job_config"]
    param_names = [p.name for p in job_config_hybrid.query_parameters]

    # Native hybrid mode checks (Form 1 single query)
    assert "FROM VECTOR_SEARCH(" in sql_hybrid
    assert "query_value => @query_vector" in sql_hybrid
    assert "lexical_search_columns => ['title', 'content', 'keywords']" in sql_hybrid
    assert "lexical_search_query_value => @query_text" in sql_hybrid
    assert "query_text" in param_names
    
    # Verify complete elimination of incorrect AI.SEARCH / Form 2 parameters
    assert "mode => 'HYBRID'" not in sql_hybrid
    assert "query_text_column" not in sql_hybrid
    assert "(SELECT @query_vector" not in sql_hybrid
    
    # Verify complete elimination of manual tokenizer / LIKE CONCAT
    assert "LIKE CONCAT" not in sql_hybrid
    assert "query_tokens_param" not in param_names
    assert "WITH vector_matches AS" not in sql_hybrid

    # Parameter value check
    query_text_param = next(p for p in job_config_hybrid.query_parameters if p.name == "query_text")
    assert query_text_param.value == query_str

    # 2. Test with hybrid_search_enabled = False (Pure Vector mode)
    mock_bq.reset_mock()
    monkeypatch.setattr(
        "agent_core.tools.enterprise_rag_mcp.knowledge_store.get_retrieval_config",
        lambda: {"fraction_lists_to_search": 0.05, "hybrid_search_enabled": False}
    )

    store.search(query_str, security_context=test_sec_ctx, system="ERP", limit=3)
    sql_pure_vec = mock_bq.query.call_args[0][0]
    job_config_pure = mock_bq.query.call_args[1]["job_config"]
    param_names_pure = [p.name for p in job_config_pure.query_parameters]

    assert "FROM VECTOR_SEARCH(" in sql_pure_vec
    assert "query_value => @query_vector" in sql_pure_vec
    assert "lexical_search_columns" not in sql_pure_vec
    assert "lexical_search_query_value" not in sql_pure_vec
    assert "mode => 'HYBRID'" not in sql_pure_vec
    assert "query_text_column" not in sql_pure_vec
    assert "(SELECT @query_vector" not in sql_pure_vec
    assert "query_text" not in param_names_pure
    assert "LIKE CONCAT" not in sql_pure_vec

    # Proves the hybrid flag alters generated SQL behavior in BigQuery production branch
    assert sql_hybrid != sql_pure_vec


def test_vietnamese_query_produces_no_token_fragmentation_noise(test_sec_ctx):
    """
    🔴 P0 VIETNAMESE TOKENIZATION TEST:
    Verifies that complex non-ASCII Vietnamese queries ("lỗi phân quyền đơn hàng")
    pass cleanly to native BigQuery query_text without tokenizer splitting noise (e.g. ['PH', 'QUY', 'NG']).
    """
    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = []
    mock_bq.query.return_value = mock_query_job

    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64
    )

    vn_query = "lỗi phân quyền đơn hàng"
    store.search(vn_query, security_context=test_sec_ctx, system="ERP", limit=3)

    job_config = mock_bq.query.call_args[1]["job_config"]
    query_text_param = next(p for p in job_config.query_parameters if p.name == "query_text")
    # Preserves full authentic Vietnamese query string for BigQuery server-side NLP analyzer
    assert query_text_param.value == "lỗi phân quyền đơn hàng"



def test_in_memory_knowledge_store_section_hierarchy(test_sec_ctx):
    store = InMemoryKnowledgeStore()
    results = store.search("Purchase Order", security_context=test_sec_ctx, system="ERP", limit=1)
    assert len(results) > 0
    assert results[0].section_hierarchy is not None
    assert results[0].section_hierarchy.h1 == "Tài liệu ERP"
    assert "Tài liệu ERP" in results[0].context_path


def test_get_knowledge_store_factory(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BACKEND", "in_memory")
    store = get_knowledge_store()
    assert isinstance(store, InMemoryKnowledgeStore)

    monkeypatch.setenv("KNOWLEDGE_BACKEND", "bigquery")
    store_bq = get_knowledge_store()
    assert isinstance(store_bq, BigQueryVectorKnowledgeStore)


def test_hybrid_search_boosts_exact_transaction_codes(test_sec_ctx):
    """
    P2.6 Hybrid Search:
    Verifies that exact transaction codes (M_BEST_EKO, ME21N, OB52) receive relevance boosts
    and rank at position 1.
    """
    store = InMemoryKnowledgeStore()

    # Query with exact authorization object M_BEST_EKO
    results_eko = store.search("Không thể duyệt PO do mã M_BEST_EKO", security_context=test_sec_ctx, system="ERP", limit=3)
    assert len(results_eko) > 0
    assert results_eko[0].article_id == "ERP-KB-001"
    assert results_eko[0].relevance_score >= 0.8

    # Query with transaction code OB52
    results_ob52 = store.search("Lỗi kỳ kế toán đóng OB52", security_context=test_sec_ctx, system="ERP", limit=3)
    assert len(results_ob52) > 0
    assert results_ob52[0].article_id == "ERP-KB-002"
    assert results_ob52[0].relevance_score >= 0.8


def test_content_governance_date_filtering(test_sec_ctx):
    """
    P2.7 Content Governance Metadata:
    Verifies that expired documents (expiry_date < today) and future documents (effective_date > today)
    are excluded from retrieval.
    """
    # Create isolated store
    expired_article = KnowledgeArticle(
        id="ERP-EXPIRED-999",
        system="ERP",
        title="Quy trình Mua hàng 2020 Cũ",
        category="Procurement",
        content="Quy trình mua hàng cũ hết hiệu lực...",
        keywords=["sap", "po", "procurement"],
        source_uri="docs/old_procurement.md",
        effective_date="2020-01-01",
        expiry_date="2021-01-01",  # Expired
        is_deleted=False,
    )

    future_article = KnowledgeArticle(
        id="ERP-FUTURE-999",
        system="ERP",
        title="Quy trình Mua hàng 2030 Tương lai",
        category="Procurement",
        content="Quy trình mua hàng tương lai chưa áp dụng...",
        keywords=["sap", "po", "future"],
        source_uri="docs/future_procurement.md",
        effective_date="2035-01-01",  # Future
        expiry_date=None,
        is_deleted=False,
    )

    store = InMemoryKnowledgeStore(articles=[expired_article, future_article])

    # Search: Neither expired nor future articles should appear
    results = store.search("Quy trình mua hàng", security_context=test_sec_ctx, system="ERP")
    retrieved_ids = [r.article_id for r in results]
    assert "ERP-EXPIRED-999" not in retrieved_ids
    assert "ERP-FUTURE-999" not in retrieved_ids


def test_search_result_metadata_propagation(test_sec_ctx):
    """
    P1.3 Metadata Flow & Traceability:
    Verifies that SearchResult contains valid source_uri, category, keywords, owner, effective_date,
    and does NOT default to 'built-in' or null when metadata exists in store.
    """
    store = InMemoryKnowledgeStore()
    results = store.search("Purchase Order phân quyền", security_context=test_sec_ctx, system="ERP", limit=1)
    assert len(results) == 1
    sr = results[0]
    assert sr.source_uri == "docs/erp_po_manual.md"
    assert sr.source_uri != "built-in"
    assert sr.source_uri is not None
    assert "Procurement" in sr.category
    assert "sap" in sr.keywords
    assert sr.owner == "erp-team@company.com"
    assert sr.effective_date == "2025-01-01"
    assert sr.is_deleted is False


def test_mutation_removing_expiry_date_filter_leaks_expired_policy(test_sec_ctx):
    """
    MUTATION TEST:
    If expiry_date pre-filter is omitted, expired documents leak into search results (RED).
    """
    expired_article = KnowledgeArticle(
        id="EXPIRED_TAX_GUIDE",
        system="ERP",
        title="Biểu thuế VAT 2021 Hết hiệu lực",
        category="Finance",
        content="Hướng dẫn kê khai thuế VAT 10% cho mặt hàng ưu đãi năm 2021...",
        source_uri="docs/expired_tax.md",
        effective_date="2021-01-01",
        expiry_date="2021-12-31",
        is_deleted=False,
    )
    store = InMemoryKnowledgeStore(articles=[expired_article])

    # With proper filtering, returns 0 results
    clean_results = store.search("Biểu thuế VAT", security_context=test_sec_ctx, system="ERP")
    assert "EXPIRED_TAX_GUIDE" not in [r.article_id for r in clean_results]


def test_mutation_removing_source_uri_breaks_citation_integrity(test_sec_ctx):
    """
    MUTATION TEST:
    If source_uri is missing from SearchResult, downstream agent citations cannot be grounded.
    """
    store = InMemoryKnowledgeStore()
    results = store.search("Timesheet", security_context=test_sec_ctx, system="HRM", limit=1)
    assert len(results) > 0
    assert results[0].source_uri is not None
    assert results[0].source_uri == "docs/hrm_timesheet_sync.md"


def test_hybrid_search_enabled_unified_default_across_backends(monkeypatch, test_sec_ctx):
    """
    P1.3 Hybrid Search Default Parity:
    Verifies that when hybrid_search_enabled is omitted from config,
    both InMemoryKnowledgeStore and BigQueryVectorKnowledgeStore resolve to True (Native HYBRID mode).
    """
    monkeypatch.setattr(
        "agent_core.tools.enterprise_rag_mcp.knowledge_store.get_retrieval_config",
        lambda: {"fraction_lists_to_search": 0.05}  # key hybrid_search_enabled missing
    )

    # BigQuery store check
    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = []
    mock_bq.query.return_value = mock_query_job

    store_bq = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64
    )

    store_bq.search("Lỗi ME21N", security_context=test_sec_ctx, system="ERP", limit=3)
    sql_bq = mock_bq.query.call_args[0][0]
    job_config_bq = mock_bq.query.call_args[1]["job_config"]
    param_names = [p.name for p in job_config_bq.query_parameters]

    # Since default is True, native BigQuery hybrid SQL must be generated
    assert "query_value => @query_vector" in sql_bq
    assert "lexical_search_columns => ['title', 'content', 'keywords']" in sql_bq
    assert "lexical_search_query_value => @query_text" in sql_bq
    assert "query_text" in param_names


def test_hybrid_search_long_query_native_parameterization(monkeypatch, test_sec_ctx):
    """
    🔴 P0 NATIVE HYBRID SEARCH LONG QUERY TEST:
    Verifies that a complex 50-word technical query passes cleanly to @query_text parameter
    for BigQuery's native full-text/BM25 analyzer without artificial token truncation.
    """
    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = []
    mock_bq.query.return_value = mock_query_job

    store_bq = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64
    )

    long_50_word_query = (
        "hướng dẫn xử lý lỗi khi người dùng không thể tạo đơn đặt hàng purchase order "
        "trên phân hệ SAP ERP với mã giao dịch ME21N và bị chặn bởi authorization object "
        "M_BEST_EKO cùng mã lỗi ZFI_POSTING_001 trong khi thực hiện giao dịch kế toán tài chính "
        "kỳ đóng OB52 và các thiết lập tài khoản phụ cấp chi tiết"
    )

    store_bq.search(long_50_word_query, security_context=test_sec_ctx, system="ERP", limit=3)
    job_config = mock_bq.query.call_args[1]["job_config"]
    query_text_param = next(p for p in job_config.query_parameters if p.name == "query_text")

    assert query_text_param.value == long_50_word_query.strip()



def test_in_memory_multi_chunk_aggregation():
    """
    P1.11 Multi-chunk Aggregation Test (InMemory):
    Verifies that get_article_by_id stitches chunks of a partitioned document in chunk_index order.
    """
    chunk0 = KnowledgeArticle(
        id="ERP-GUIDE-001#chunk-0",
        parent_doc_id="ERP-GUIDE-001",
        chunk_index=0,
        system="ERP",
        title="Hướng dẫn ERP Tổng quát (Phần 1/2)",
        category="Manual",
        content="Nội dung phần 1: Thiết lập cấu hình hệ thống.",
        section_h1="Chương 1",
    )
    chunk1 = KnowledgeArticle(
        id="ERP-GUIDE-001#chunk-1",
        parent_doc_id="ERP-GUIDE-001",
        chunk_index=1,
        system="ERP",
        title="Hướng dẫn ERP Tổng quát (Phần 2/2)",
        category="Manual",
        content="Nội dung phần 2: Vận hành và xử lý lỗi thường gặp.",
        section_h1="Chương 2",
    )
    store = InMemoryKnowledgeStore(articles=[chunk1, chunk0])  # out of order
    user_sec = SecurityContext.from_user(roles=["employee"], clearance_level=3)

    # Lookup by parent_doc_id
    doc = store.get_article_by_id("ERP-GUIDE-001", security_context=user_sec)
    assert doc is not None
    assert doc.id == "ERP-GUIDE-001"
    assert "Nội dung phần 1" in doc.content
    assert "Nội dung phần 2" in doc.content
    # Verified ordered concatenation
    assert doc.content.index("Nội dung phần 1") < doc.content.index("Nội dung phần 2")

    # Lookup by chunk ID also stitches full doc
    doc_by_chunk = store.get_article_by_id("ERP-GUIDE-001#chunk-0", security_context=user_sec)
    assert doc_by_chunk is not None
    assert "Nội dung phần 1" in doc_by_chunk.content
    assert "Nội dung phần 2" in doc_by_chunk.content


def test_bigquery_get_article_by_id_multi_chunk():
    """
    P1.11 Multi-chunk Aggregation Test (BigQuery):
    Verifies that BigQueryVectorKnowledgeStore.get_article_by_id stitches chunks returned by SQL.
    """
    mock_bq = MagicMock()
    from types import SimpleNamespace
    row0 = SimpleNamespace(
        id="DOC-100#chunk-0",
        parent_doc_id="DOC-100",
        chunk_index=0,
        system="HRM",
        title="Quy chế nghỉ phép (Phần 1)",
        category="Policy",
        content="Phần 1: Số ngày phép tiêu chuẩn.",
        section_h1="Nghỉ phép",
        section_h2="Tiêu chuẩn",
        section_h3=None,
        allowed_roles=["ALL_EMPLOYEES"],
        sensitivity="INTERNAL",
        source_uri="docs/leave_policy.docx",
        owner="hr@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
        deleted_at=None,
        keywords=["nghi_phep", "che_do"],
    )
    row1 = SimpleNamespace(
        id="DOC-100#chunk-1",
        parent_doc_id="DOC-100",
        chunk_index=1,
        system="HRM",
        title="Quy chế nghỉ phép (Phần 2)",
        category="Policy",
        content="Phần 2: Thủ tục bàn giao công việc.",
        section_h1="Nghỉ phép",
        section_h2="Thủ tục",
        section_h3=None,
        allowed_roles=["ALL_EMPLOYEES"],
        sensitivity="INTERNAL",
        source_uri="docs/leave_policy.docx",
        owner="hr@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
        deleted_at=None,
        keywords=["nghi_phep", "che_do"],
    )

    mock_query_job = MagicMock()
    mock_query_job.result.return_value = [row0, row1]
    mock_bq.query.return_value = mock_query_job

    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
    )

    doc = store.get_article_by_id("DOC-100", security_context=SecurityContext.from_user(roles=["ALL_EMPLOYEES"], clearance_level=3))
    assert doc is not None
    assert doc.id == "DOC-100"
    assert "Phần 1: Số ngày phép tiêu chuẩn." in doc.content
    assert "Phần 2: Thủ tục bàn giao công việc." in doc.content
    assert doc.content.index("Phần 1") < doc.content.index("Phần 2")


def test_vector_index_active_check_conditional_options(monkeypatch, test_sec_ctx):
    """
    P0.3 Conditional fraction_lists_to_search:
    Verifies that options => '{"fraction_lists_to_search": ...}' is ONLY added when index status is ACTIVE and coverage >= 95.0.
    """
    mock_bq = MagicMock()
    from types import SimpleNamespace

    # Mock index coverage check query: below 95% -> Inactive
    cov_job = MagicMock()
    cov_job.result.return_value = [SimpleNamespace(index_status="ACTIVE", coverage_percentage=80.0)]
    search_job = MagicMock()
    search_job.result.return_value = []

    mock_bq.query.side_effect = [cov_job, search_job]

    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64,
    )

    store.search("Lỗi kiểm thử", security_context=test_sec_ctx, system="ERP", limit=2)
    # Search SQL should NOT contain fraction_lists_to_search when index coverage is < 95.0
    search_sql = mock_bq.query.call_args_list[1][0][0]
    assert "fraction_lists_to_search" not in search_sql


def test_is_vector_index_active_fail_closed_and_threshold():
    """Verify that _is_vector_index_active returns False on empty result, error, or coverage < 95.0, and True when ACTIVE and >= 95.0."""
    from types import SimpleNamespace
    mock_bq = MagicMock()
    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64,
    )

    # 1. Empty rows -> False
    job_empty = MagicMock()
    job_empty.result.return_value = []
    mock_bq.query.return_value = job_empty
    store._index_active_cache = None
    assert store._is_vector_index_active() is False

    # 2. Exception -> False (fail-closed)
    mock_bq.query.side_effect = RuntimeError("BigQuery connection error")
    store._index_active_cache = None
    assert store._is_vector_index_active() is False
    mock_bq.query.side_effect = None

    # 3. Status PENDING / coverage 100 -> False
    job_pending = MagicMock()
    job_pending.result.return_value = [SimpleNamespace(index_status="PENDING", coverage_percentage=100.0)]
    mock_bq.query.return_value = job_pending
    store._index_active_cache = None
    assert store._is_vector_index_active() is False

    # 4. Status ACTIVE but coverage 94.9% -> False
    job_low = MagicMock()
    job_low.result.return_value = [SimpleNamespace(index_status="ACTIVE", coverage_percentage=94.9)]
    mock_bq.query.return_value = job_low
    store._index_active_cache = None
    assert store._is_vector_index_active() is False

    # 5. Status ACTIVE and coverage 95.0% -> True
    job_ok = MagicMock()
    job_ok.result.return_value = [SimpleNamespace(index_status="ACTIVE", coverage_percentage=95.0)]
    mock_bq.query.return_value = job_ok
    store._index_active_cache = None
    assert store._is_vector_index_active() is True


def test_bigquery_knowledge_store_missing_library_raises_importerror():
    """Verify that BigQueryVectorKnowledgeStore fails loud with ImportError when google-cloud-bigquery is not importable."""
    import builtins
    orig_import = builtins.__import__

    def mock_import(name, globals=None, locals=None, fromlist=(), level=0):
        if (fromlist and "bigquery" in fromlist) or ("bigquery" in name):
            raise ImportError("No module named 'google.cloud.bigquery'")
        return orig_import(name, globals, locals, fromlist, level)

    with patch("builtins.__import__", side_effect=mock_import):
        with pytest.raises(ImportError) as exc_info:
            BigQueryVectorKnowledgeStore(project_id="test-proj")
        assert "google-cloud-bigquery" in str(exc_info.value)


def test_bigquery_telemetry_and_job_timeout_cancel(caplog, test_sec_ctx):
    """
    0.10: Verifies that BigQueryVectorKnowledgeStore:
    1. Sets job_timeout_ms on QueryJobConfig matching timeout (e.g. 3000ms).
    2. Logs telemetry metrics: bytes_billed, bytes_processed, cache_hit, slot_ms.
    3. Calls query_job.cancel() when query_job.result() raises an exception or times out.
    """
    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.total_bytes_billed = 10485760
    mock_query_job.total_bytes_processed = 5242880
    mock_query_job.cache_hit = False
    mock_query_job.slot_millis = 150
    mock_query_job.job_id = "job_test_123"
    mock_query_job.result.return_value = []
    mock_bq.query.return_value = mock_query_job

    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64
    )

    with caplog.at_level(logging.INFO, logger="agent_core"):
        store.search("Lỗi mạng LAN", security_context=test_sec_ctx, system="ALL", limit=5)

    # 1. Verify job_timeout_ms passed in QueryJobConfig
    call_kwargs = mock_bq.query.call_args[1]
    job_cfg = call_kwargs.get("job_config")
    assert job_cfg is not None
    assert int(job_cfg.job_timeout_ms) == 3000

    # 2. Verify Telemetry logging
    assert any(
        "bytes_billed=10485760" in rec.message and "slot_ms=150" in rec.message
        for rec in caplog.records
    )

    # 3. Verify query_job.cancel() on timeout / exception
    mock_fail_job = MagicMock()
    mock_fail_job.result.side_effect = TimeoutError("Query job execution exceeded 3.0s")
    mock_bq.query.return_value = mock_fail_job

    with pytest.raises(KnowledgeStoreUnavailableError):
        store.search("Lỗi mạng LAN", security_context=test_sec_ctx, system="ALL", limit=5)

    mock_fail_job.cancel.assert_called_once()


def test_vertex_ai_search_store_initialization():
    """Verifies that VertexAISearchKnowledgeStore properly configures resource paths."""
    store = VertexAISearchKnowledgeStore(
        project_id="my-gcp-project",
        location="asia-southeast1",
        data_store_id="my-datastore",
        serving_config_id="my-search-config",
        collection_id="my-collection",
    )
    assert isinstance(store, BaseKnowledgeStore)
    expected_path = (
        "projects/my-gcp-project/locations/asia-southeast1/collections/"
        "my-collection/dataStores/my-datastore/servingConfigs/my-search-config"
    )
    assert store._get_serving_config_path() == expected_path


def test_vertex_ai_search_store_search_single_system(test_sec_ctx):
    """Verifies search with single system filter, extraction of snippets and metadata."""
    from types import SimpleNamespace
    mock_client = MagicMock()

    mock_doc = SimpleNamespace(
        id="ERP-DOC-001",
        struct_data={
            "id": "ERP-KB-001",
            "title": "Hướng dẫn cấp quyền ERP",
            "system": "ERP",
            "category": "Security",
            "section_h1": "ERP Guide",
            "section_h2": "Authorization",
            "allowed_roles": ["it_admin", "user"],
            "sensitivity": "INTERNAL",
            "owner": "erp-admin@company.com",
            "effective_date": "2025-01-01",
            "is_deleted": False,
        },
        derived_struct_data={
            "extractive_segments": [{"content": "Quy trình cấp quyền ME21N trên SAP..."}],
            "snippets": [{"snippet": "Quy trình cấp quyền tcode..."}],
        },
    )
    mock_item = SimpleNamespace(
        document=mock_doc,
        model_scores={"relevance": 0.92},
        relevance_score=0.92,
    )
    mock_response = SimpleNamespace(results=[mock_item])
    mock_client.search.return_value = mock_response

    store = VertexAISearchKnowledgeStore(
        project_id="test-proj",
        location="global",
        data_store_id="test-store",
        search_client=mock_client,
    )

    results = store.search("cấp quyền SAP", security_context=test_sec_ctx, system="ERP", limit=2)
    assert len(results) == 1
    assert results[0].article_id == "ERP-KB-001"
    assert results[0].title == "Hướng dẫn cấp quyền ERP"
    assert "Quy trình cấp quyền ME21N" in results[0].snippet
    assert results[0].system == "ERP"
    assert results[0].relevance_score == 0.92
    assert results[0].section_hierarchy.h1 == "ERP Guide"
    assert results[0].context_path == "ERP Guide > Authorization"
    assert "it_admin" in results[0].allowed_roles

    # Verify search request arguments
    assert mock_client.search.called
    req = mock_client.search.call_args[1].get("request")
    assert req.query == "cấp quyền SAP"
    assert req.filter == 'system: ANY("ERP")'
    assert req.page_size == 2


def test_vertex_ai_search_store_search_allowed_systems(test_sec_ctx):
    """Verifies search with allowed_systems security trimming."""
    from types import SimpleNamespace
    mock_client = MagicMock()
    mock_response = SimpleNamespace(results=[])
    mock_client.search.return_value = mock_response

    store = VertexAISearchKnowledgeStore(
        project_id="test-proj",
        location="global",
        data_store_id="test-store",
        search_client=mock_client,
    )

    results = store.search(
        "chính sách làm việc",
        security_context=test_sec_ctx,
        system="ALL",
        limit=3,
        allowed_systems=["HRM", "ERP"],
    )
    assert results == []
    assert mock_client.search.called
    req = mock_client.search.call_args[1].get("request")
    assert req.filter == 'system: ANY("HRM", "ERP")'


def test_vertex_ai_search_store_get_article_by_id():
    """Verifies get_article_by_id retrieval."""
    from types import SimpleNamespace
    mock_client = MagicMock()

    mock_doc = SimpleNamespace(
        id="HRM-DOC-100",
        struct_data={
            "id": "HRM-KB-100",
            "title": "Quy trình nghỉ phép",
            "system": "HRM",
            "category": "Policy",
            "content": "Nội dung quy trình nghỉ phép năm...",
            "allowed_roles": ["employee"],
        },
        derived_struct_data={},
    )
    mock_item = SimpleNamespace(
        document=mock_doc,
        model_scores={"relevance": 1.0},
    )
    mock_response = SimpleNamespace(results=[mock_item])
    mock_client.search.return_value = mock_response

    store = VertexAISearchKnowledgeStore(
        project_id="test-proj",
        location="global",
        data_store_id="test-store",
        search_client=mock_client,
    )
    user_sec = SecurityContext.from_user(roles=["employee"], clearance_level=3)

    article = store.get_article_by_id("HRM-KB-100", security_context=user_sec)
    assert article is not None
    assert article.id == "HRM-KB-100"
    assert article.title == "Quy trình nghỉ phép"
    assert article.system == "HRM"

    # Not found
    not_found_article = store.get_article_by_id("NON_EXISTENT", security_context=user_sec)
    assert not_found_article is None

    # Empty ID
    assert store.get_article_by_id("", security_context=user_sec) is None


def test_vertex_ai_search_store_fail_closed_on_error(test_sec_ctx):
    """Verifies that VertexAISearchKnowledgeStore raises KnowledgeStoreUnavailableError on failures."""
    mock_client = MagicMock()
    mock_client.search.side_effect = TimeoutError("Vertex AI Search API timed out after 5.0s")

    store = VertexAISearchKnowledgeStore(
        project_id="test-proj",
        location="global",
        data_store_id="test-store",
        search_client=mock_client,
    )

    with pytest.raises(KnowledgeStoreUnavailableError) as exc_info:
        store.search("Lỗi SAP", security_context=test_sec_ctx, system="ERP")
    assert "Vertex AI Search" in str(exc_info.value)


def test_get_knowledge_store_factory_vertex_ai_search():
    """Verifies that get_knowledge_store returns VertexAISearchKnowledgeStore when configured."""
    with patch.dict(os.environ, {"KNOWLEDGE_BACKEND": "vertex_ai_search"}):
        store = get_knowledge_store()
        assert isinstance(store, VertexAISearchKnowledgeStore)

    with patch.dict(os.environ, {"KNOWLEDGE_BACKEND": "discoveryengine"}):
        store = get_knowledge_store()
        assert isinstance(store, VertexAISearchKnowledgeStore)

    with patch.dict(os.environ, {"KNOWLEDGE_BACKEND": "bigquery"}):
        with patch("google.cloud.bigquery.Client"):
            store = get_knowledge_store()
            assert isinstance(store, BigQueryVectorKnowledgeStore)

    with patch.dict(os.environ, {"KNOWLEDGE_BACKEND": "in_memory"}):
        store = get_knowledge_store()
        assert isinstance(store, InMemoryKnowledgeStore)


def test_get_article_by_id_authorization_matrix_across_all_backends():
    """
    CRITICAL SECURITY TEST:
    Verifies that get_article_by_id strictly enforces RBAC, clearance level, tombstone,
    and expiration across all 3 backends (InMemory, BigQuery, and Vertex AI Search).
    """
    from types import SimpleNamespace

    # 1. InMemoryKnowledgeStore Matrix
    secret_article = KnowledgeArticle(
        id="SEC-001",
        system="ERP",
        title="Bí mật thanh toán tài chính",
        category="Finance",
        content="Nội dung bảo mật cấp cao...",
        allowed_roles=["finance_admin"],
        sensitivity="RESTRICTED",
        clearance_level=3,
        effective_date="2025-01-01",
        expiry_date="2099-01-01",
        is_deleted=False,
    )
    tombstoned_article = KnowledgeArticle(
        id="DEL-001",
        system="HRM",
        title="Quy chế cũ đã xóa",
        category="Policy",
        content="Quy chế cũ không còn hiệu lực...",
        allowed_roles=[],
        sensitivity="PUBLIC",
        clearance_level=1,
        is_deleted=True,
    )
    expired_article = KnowledgeArticle(
        id="EXP-001",
        system="HRM",
        title="Quy chế đã hết hạn",
        category="Policy",
        content="Quy chế này đã hết hạn năm 2020...",
        allowed_roles=[],
        sensitivity="PUBLIC",
        clearance_level=1,
        expiry_date="2020-01-01",
        is_deleted=False,
    )
    in_mem_store = InMemoryKnowledgeStore(articles=[secret_article, tombstoned_article, expired_article])

    admin_sec = SecurityContext.from_user(user_id="fin-admin", roles=["finance_admin"], clearance_level=3)
    employee_sec = SecurityContext.from_user(user_id="emp", roles=["employee"], clearance_level=1)
    no_clearance_sec = SecurityContext.from_user(user_id="fin-no-clearance", roles=["finance_admin"], clearance_level=1)

    # InMemory: Authorized user gets article
    assert in_mem_store.get_article_by_id("SEC-001", security_context=admin_sec) is not None
    # InMemory: Unauthorized role gets None
    assert in_mem_store.get_article_by_id("SEC-001", security_context=employee_sec) is None
    # InMemory: Insufficient clearance gets None
    assert in_mem_store.get_article_by_id("SEC-001", security_context=no_clearance_sec) is None
    # InMemory: Tombstoned article gets None
    assert in_mem_store.get_article_by_id("DEL-001", security_context=admin_sec) is None
    # InMemory: Expired article gets None
    assert in_mem_store.get_article_by_id("EXP-001", security_context=admin_sec) is None

    # 2. BigQueryVectorKnowledgeStore Matrix
    mock_bq = MagicMock()
    bq_row = SimpleNamespace(
        id="SEC-BQ-001",
        parent_doc_id=None,
        chunk_index=None,
        system="ERP",
        title="Cấu hình bảo mật SAP BigQuery",
        category="Security",
        content="Nội dung nhạy cảm phân quyền...",
        section_h1=None,
        section_h2=None,
        section_h3=None,
        allowed_roles=["sec_admin"],
        sensitivity="RESTRICTED",
        clearance_level=3,
        source_uri="docs/sec.md",
        owner="sec@company.com",
        effective_date="2025-01-01",
        expiry_date="2099-01-01",
        is_deleted=False,
        deleted_at=None,
        keywords=["security", "sap"],
    )
    mock_job = MagicMock()
    mock_job.result.return_value = [bq_row]
    mock_bq.query.return_value = mock_job

    bq_store = BigQueryVectorKnowledgeStore(
        project_id="test-proj",
        dataset_id="test_ds",
        table_name="articles",
        bq_client=mock_bq,
    )
    sec_admin_ctx = SecurityContext.from_user(user_id="sec-admin", roles=["sec_admin"], clearance_level=3)
    emp_ctx = SecurityContext.from_user(user_id="emp", roles=["employee"], clearance_level=1)

    # BigQuery: Authorized user gets article
    bq_art = bq_store.get_article_by_id("SEC-BQ-001", security_context=sec_admin_ctx)
    assert bq_art is not None
    assert bq_art.id == "SEC-BQ-001"
    # BigQuery: Unauthorized user gets None (fail closed via post-query authorization)
    assert bq_store.get_article_by_id("SEC-BQ-001", security_context=emp_ctx) is None

    # 3. VertexAISearchKnowledgeStore Matrix
    mock_search_client = MagicMock()
    v_doc = SimpleNamespace(
        id="SEC-VERTEX-001",
        struct_data={
            "id": "SEC-VERTEX-001",
            "title": "Bảo mật cấp cao Vertex AI Search",
            "system": "ERP",
            "category": "Security",
            "content": "Nội dung bảo mật Vertex...",
            "allowed_roles": ["vertex_admin"],
            "sensitivity": "RESTRICTED",
            "clearance_level": 3,
        },
        derived_struct_data={},
    )
    v_item = SimpleNamespace(
        document=v_doc,
        model_scores={"relevance": 1.0},
    )
    v_resp = SimpleNamespace(results=[v_item])
    mock_search_client.search.return_value = v_resp

    vertex_store = VertexAISearchKnowledgeStore(
        project_id="test-proj",
        location="global",
        data_store_id="test-store",
        search_client=mock_search_client,
    )
    v_admin_ctx = SecurityContext.from_user(user_id="v-admin", roles=["vertex_admin"], clearance_level=3)

    # Vertex: Authorized user gets article
    v_art = vertex_store.get_article_by_id("SEC-VERTEX-001", security_context=v_admin_ctx)
    assert v_art is not None
    assert v_art.id == "SEC-VERTEX-001"
    # Vertex: Unauthorized user gets None
    assert vertex_store.get_article_by_id("SEC-VERTEX-001", security_context=emp_ctx) is None


def test_normalize_similarity_scale_and_metrics():
    """
    P1-03 [R2]: Tests similarity formula scaling across standard cosine distances [0.0, 2.0]
    and verifies proper bounding [0.0, 1.0], rounding (4 decimals), and alternative metrics.
    """
    # 1. Cosine Distance Canonical Test Points
    assert normalize_similarity(0.0, metric="COSINE") == 1.0
    assert normalize_similarity(0.5, metric="COSINE") == 0.75
    assert normalize_similarity(1.0, metric="COSINE") == 0.5
    assert normalize_similarity(1.5, metric="COSINE") == 0.25
    assert normalize_similarity(2.0, metric="COSINE") == 0.0

    # 2. Precision and rounding (4 decimal places)
    assert normalize_similarity(0.333333, metric="COSINE") == 0.8333

    # 3. Clamping boundary checks
    assert normalize_similarity(-0.5, metric="COSINE") == 1.0
    assert normalize_similarity(2.5, metric="COSINE") == 0.0

    # 4. Euclidean Metric
    assert normalize_similarity(0.0, metric="EUCLIDEAN") == 1.0
    assert normalize_similarity(1.0, metric="EUCLIDEAN") == 0.5
    assert normalize_similarity(3.0, metric="EUCLIDEAN") == 0.25

    # 5. Invalid / Non-numeric distance fails closed to 0.0
    assert normalize_similarity("invalid", metric="COSINE") == 0.0
    assert normalize_similarity(None, metric="COSINE") == 0.0


def test_bigquery_hybrid_search_preserves_fused_ranking_and_omits_order_by(monkeypatch, test_sec_ctx):
    """
    P1-03 [R2]: Verifies that when hybrid_search_enabled is True, BigQuery VECTOR_SEARCH
    omits 'ORDER BY distance ASC' so that BigQuery's fused hybrid ranking (combining
    lexical BM25 and vector score) is preserved.
    """
    from types import SimpleNamespace

    # Create two mock rows where row 1 has a higher hybrid rank from BigQuery even with larger vector distance
    mock_row_1 = SimpleNamespace(
        id="HYBRID-001",
        parent_doc_id=None,
        chunk_index=None,
        system="ERP",
        title="Tài liệu Hybrid 1",
        category="General",
        content="Nội dung khớp chính xác từ khóa và vector",
        section_h1="Guide",
        section_h2=None,
        section_h3=None,
        section_hierarchy=None,
        allowed_roles=[],
        sensitivity="INTERNAL",
        source_uri=None,
        owner="admin@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
        keywords=["m_best_eko"],
        distance=0.40,  # Larger vector distance, but ranked first by BigQuery hybrid fusion
        clearance_level=1,
    )
    mock_row_2 = SimpleNamespace(
        id="HYBRID-002",
        parent_doc_id=None,
        chunk_index=None,
        system="ERP",
        title="Tài liệu Hybrid 2",
        category="General",
        content="Nội dung chỉ khớp vector",
        section_h1="Guide",
        section_h2=None,
        section_h3=None,
        section_hierarchy=None,
        allowed_roles=[],
        sensitivity="INTERNAL",
        source_uri=None,
        owner="admin@company.com",
        effective_date="2025-01-01",
        expiry_date=None,
        is_deleted=False,
        keywords=[],
        distance=0.20,  # Smaller vector distance, but ranked second by BigQuery hybrid fusion
        clearance_level=1,
    )

    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.result.return_value = [mock_row_1, mock_row_2]
    mock_bq.query.return_value = mock_query_job

    store = BigQueryVectorKnowledgeStore(
        project_id="test-project",
        dataset_id="test_kb",
        table_name="articles",
        bq_client=mock_bq,
        embedding_fn=lambda t: [0.1] * 64,
    )

    # 1. Hybrid Search Enabled: ORDER BY distance ASC must NOT be present
    monkeypatch.setattr(
        "agent_core.tools.enterprise_rag_mcp.knowledge_store.get_retrieval_config",
        lambda: {"fraction_lists_to_search": 0.05, "hybrid_search_enabled": True}
    )
    results_hybrid = store.search("m_best_eko", security_context=test_sec_ctx, system="ERP", limit=2)
    sql_hybrid = mock_bq.query.call_args[0][0]
    assert "ORDER BY distance ASC" not in sql_hybrid
    # BigQuery's hybrid fused order (row 1 first, row 2 second) must be preserved
    assert len(results_hybrid) == 2
    assert results_hybrid[0].article_id == "HYBRID-001"
    assert results_hybrid[0].relevance_score == 0.80  # 1.0 - (0.4 / 2.0)
    assert results_hybrid[1].article_id == "HYBRID-002"
    assert results_hybrid[1].relevance_score == 0.90  # 1.0 - (0.2 / 2.0)

    # 2. Hybrid Search Disabled (Pure Vector): ORDER BY distance ASC MUST be present
    mock_bq.reset_mock()
    monkeypatch.setattr(
        "agent_core.tools.enterprise_rag_mcp.knowledge_store.get_retrieval_config",
        lambda: {"fraction_lists_to_search": 0.05, "hybrid_search_enabled": False}
    )
    store.search("m_best_eko", security_context=test_sec_ctx, system="ERP", limit=2)
    sql_pure = mock_bq.query.call_args[0][0]
    assert "ORDER BY distance ASC" in sql_pure






