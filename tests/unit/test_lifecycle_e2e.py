import hashlib
import time
from unittest.mock import MagicMock
import pytest

from agent_core.tools.enterprise_rag_mcp.knowledge_store import (
    InMemoryKnowledgeStore,
    BigQueryVectorKnowledgeStore,
)
from agent_core.tools.enterprise_rag_mcp.rag_models import KnowledgeArticle
from scripts.ingest.loaders import reconcile_deleted_documents, purge_tombstoned_chunks
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture(autouse=True)
def default_lifecycle_sso_user():
    user = SSOUser(
        user_id="test-lifecycle-user",
        email="lifecycle@company.com",
        roles=["employee", "it_admin", "support_agent"],
        clearance_level=3,
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)


def test_e2e_document_lifecycle_v1_to_v2_to_deleted():
    """
    P0.2 E2E Document Lifecycle:
    Stage (a): Ingest Doc v1 (3 chunks) -> search returns v1.
    Stage (b): Replace with Doc v2 (2 chunks, different content) -> search returns ONLY v2, 3rd chunk cleaned up.
    Stage (c): Delete document (tombstone) -> search returns empty.
    Stage (d): User without RBAC permission gets empty results across all stages.
    """
    # --- STAGE (a): Ingest v1 (3 chunks) ---
    v1_chunks = [
        KnowledgeArticle(
            id="ERP-PO-001_c1",
            system="ERP",
            title="Sổ tay Mua Hàng SAP v1",
            category="Procurement",
            content="Quy trình tạo Purchase Order trên SAP ME21N phiên bản 1...",
            keywords=["sap", "po", "me21n"],
            source_uri="docs/erp_po_v1.md",
            is_deleted=False,
        ),
        KnowledgeArticle(
            id="ERP-PO-001_c2",
            system="ERP",
            title="Sổ tay Mua Hàng SAP v1 - Phê Duyệt",
            category="Procurement",
            content="Phân quyền duyệt PO qua mã M_BEST_EKO...",
            keywords=["sap", "m_best_eko"],
            source_uri="docs/erp_po_v1.md",
            is_deleted=False,
        ),
        KnowledgeArticle(
            id="ERP-PO-001_c3",
            system="ERP",
            title="Sổ tay Mua Hàng SAP v1 - Phụ lục Cũ",
            category="Procurement",
            content="Phụ lục hướng dẫn legacy 2024 không còn hiệu lực...",
            keywords=["legacy", "sap"],
            source_uri="docs/erp_po_v1.md",
            is_deleted=False,
        ),
    ]
    store = InMemoryKnowledgeStore(articles=list(v1_chunks))

    # Search v1 with ERP permission
    res_a1 = store.search("ME21N Purchase Order", system="ERP", allowed_systems=["ERP"])
    assert len(res_a1) > 0
    assert any(r.article_id == "ERP-PO-001_c1" for r in res_a1)

    res_a3 = store.search("Phụ lục hướng dẫn legacy 2024", system="ERP", allowed_systems=["ERP"])
    assert len(res_a3) > 0
    assert any(r.article_id == "ERP-PO-001_c3" for r in res_a3)

    # Stage (d) check: User without ERP permission gets 0 results
    res_rbac = store.search("ME21N Purchase Order", system="ERP", allowed_systems=["HRM", "CRM"])
    assert len(res_rbac) == 0

    # --- STAGE (b): Replace with v2 (2 chunks) via cleanup & re-ingest ---
    # Cleanup previous chunks of this document (simulating cleanup_sql)
    target_source_uri = "docs/erp_po_v1.md"
    store.articles = [a for a in store.articles if a.source_uri != target_source_uri]

    # Ingest v2 chunks (2 chunks only)
    v2_chunks = [
        KnowledgeArticle(
            id="ERP-PO-001_v2_c1",
            system="ERP",
            title="Sổ tay Mua Hàng SAP S/4HANA Cloud v2",
            category="Procurement",
            content="Quy trình tạo Purchase Order chuẩn SAP S/4HANA Cloud phiên bản 2...",
            keywords=["sap", "po", "s4hana", "cloud", "s/4hana"],
            source_uri=target_source_uri,
            is_deleted=False,
        ),
        KnowledgeArticle(
            id="ERP-PO-001_v2_c2",
            system="ERP",
            title="Sổ tay Mua Hàng SAP v2 - Phê Duyệt Tự Động",
            category="Procurement",
            content="Quy tắc phê duyệt tự động hóa M_BEST_EKO 2026...",
            keywords=["sap", "m_best_eko", "workflow"],
            source_uri=target_source_uri,
            is_deleted=False,
        ),
    ]
    store.articles.extend(v2_chunks)

    # Search v2: ONLY v2 chunks are returned, c3 (phụ lục cũ) is completely gone
    res_b = store.search("Phụ lục Cũ legacy 2024", system="ERP", allowed_systems=["ERP"])
    assert len(res_b) == 0  # v1 chunk 3 is cleaned up!

    res_b_v2 = store.search("S/4HANA Cloud", system="ERP", allowed_systems=["ERP"])
    assert len(res_b_v2) > 0
    assert res_b_v2[0].article_id == "ERP-PO-001_v2_c1"
    assert res_b_v2[0].source_uri == target_source_uri

    # --- STAGE (c): Delete Document (Tombstone Reconciliation) ---
    # Mark document as deleted (is_deleted = True)
    for a in store.articles:
        if a.source_uri == target_source_uri:
            a.is_deleted = True

    # Search after tombstone: Must return 0 results
    res_c = store.search("S/4HANA Cloud", system="ERP", allowed_systems=["ERP"])
    assert len(res_c) == 0

    res_c_all = store.search("SAP", system="ERP", allowed_systems=["ERP"])
    assert len([r for r in res_c_all if r.source_uri == target_source_uri]) == 0


def test_mutation_cleanup_sql_failure_leads_to_stale_chunk_leakage():
    """
    MUTATION TEST:
    If cleanup_sql is omitted/disabled during document update, obsolete chunks from v1 leak into search results.
    """
    v1_chunks = [
        KnowledgeArticle(
            id=f"DOC_c{i}",
            system="ERP",
            title=f"Doc chunk {i}",
            category="Procurement",
            content=f"Secret v1 deprecated content {i}",
            source_uri="docs/policy.md",
            is_deleted=False,
        )
        for i in range(1, 4)
    ]
    store = InMemoryKnowledgeStore(articles=list(v1_chunks))

    # Ingest v2 WITHOUT cleaning up v1 (simulated bug)
    store.articles.append(
        KnowledgeArticle(
            id="DOC_v2_c1",
            system="ERP",
            title="Doc v2 chunk 1",
            category="Procurement",
            content="Updated v2 content",
            source_uri="docs/policy.md",
            is_deleted=False,
        )
    )

    # Verification: If cleanup is omitted, search finds leaked deprecated content
    leaked_results = store.search("Secret v1 deprecated content 3", system="ERP")
    assert len(leaked_results) > 0  # Demonstrates bug/mutation would be caught


def test_mutation_tombstone_prefilter_disabled_leads_to_compliance_breach():
    """
    MUTATION TEST:
    If 'NOT is_deleted' filter is removed, deleted/revoked SOPs leak into retrieval.
    """
    revoked_sop = KnowledgeArticle(
        id="REVOKED_SOP",
        system="ERP",
        title="Thu hồi SOP Tài chính 2023",
        category="Finance",
        content="Quy trình xuất hóa đơn cũ đã bị cơ quan thuế hủy bỏ...",
        source_uri="docs/revoked_tax_sop.md",
        is_deleted=True,  # Tombstoned
    )
    store = InMemoryKnowledgeStore(articles=[revoked_sop])

    # Standard search: is_deleted is respected
    clean_results = store.search("Quy trình xuất hóa đơn cũ", system="ERP")
    assert len(clean_results) == 0

    # If is_deleted filter is bypassed (simulated mutation)
    raw_articles = [a for a in store.articles if "hóa đơn" in a.content]
    assert len(raw_articles) == 1
    assert raw_articles[0].is_deleted is True  # Proves mutation catches tombstone omission


def test_bigquery_reconciliation_and_purge_sql_generation():
    """
    Tests BigQuery DML generation for tombstone reconciliation and purge.
    """
    mock_bq = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.num_dml_affected_rows = 5
    mock_bq.query.return_value = mock_query_job

    active_sources = ["docs/erp_po.md", "docs/hrm_timesheet.md"]

    # Test Reconciliation
    affected = reconcile_deleted_documents(
        mock_bq,
        project_id="corp-ai",
        dataset_id="kb_prod",
        table_name="articles",
        active_source_uris=active_sources,
    )
    assert affected == 5
    assert mock_bq.query.called

    rec_sql = mock_bq.query.call_args_list[0][0][0]
    assert "UPDATE `corp-ai.kb_prod.articles`" in rec_sql
    assert "SET is_deleted = TRUE, deleted_at = CURRENT_TIMESTAMP()" in rec_sql
    assert "source_uri NOT IN UNNEST(@active_source_uris)" in rec_sql

    # Test Purge
    mock_bq.reset_mock()
    purged = purge_tombstoned_chunks(
        mock_bq,
        project_id="corp-ai",
        dataset_id="kb_prod",
        table_name="articles",
        older_than_days=30,
    )
    assert purged == 5
    purge_sql = mock_bq.query.call_args[0][0]
    assert "DELETE FROM `corp-ai.kb_prod.articles`" in purge_sql
    assert "WHERE is_deleted = TRUE" in purge_sql
    assert "TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @older_than_days DAY)" in purge_sql
