import pytest
from agent_core.tools.enterprise_rag_mcp.knowledge_store import KnowledgeStore
from agent_core.tools.enterprise_rag_mcp.main import (
    search_enterprise_knowledge,
    get_system_manual,
    draft_email_response
)
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture(autouse=True)
def default_rag_admin():
    """Sets an authorized IT admin user in context for general RAG tool tests."""
    user = SSOUser(
        user_id="it-admin-01",
        email="admin@company.com",
        roles=["employee", "it_admin", "support_agent"],
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)


def test_knowledge_store_search_erp():
    store = KnowledgeStore()
    results = store.search(query="purchase order sap", system="ERP")
    assert len(results) > 0
    assert results[0].system == "ERP"
    assert "M_BEST_EKO" in results[0].snippet or "Purchase Order" in results[0].title


def test_knowledge_store_search_hrm():
    store = KnowledgeStore()
    results = store.search(query="chấm công vân tay", system="HRM")
    assert len(results) > 0
    assert results[0].system == "HRM"
    assert "HRM-KB-101" in results[0].article_id


def test_get_system_manual_success_and_not_found():
    res = get_system_manual("ERP-KB-001")
    assert res["status"] == "success"
    assert res["article"]["id"] == "ERP-KB-001"

    res_missing = get_system_manual("INVALID-ID-999")
    assert res_missing["status"] == "error"


def test_enterprise_rag_rbac_denied_for_unauthorized_role():
    from agent_core.app_utils.sso_auth import SSOUser, current_sso_user
    
    # Regular employee without HR role
    employee = SSOUser(
        user_id="emp-001",
        email="emp@company.com",
        roles=["employee"]
    )
    token = current_sso_user.set(employee)
    try:
        # Search restricted HRM domain
        res = search_enterprise_knowledge("chấm công", system="HRM")
        assert len(res) == 1
        assert "FORBIDDEN" in res[0]["article_id"] or "Access Denied" in res[0]["title"]

        # Get system manual for HRM
        manual_res = get_system_manual("HRM-KB-101")
        assert manual_res["status"] == "forbidden"
        assert manual_res["error"] == "Access Denied"
    finally:
        current_sso_user.reset(token)


def test_enterprise_rag_rbac_allowed_for_hr_role():
    from agent_core.app_utils.sso_auth import SSOUser, current_sso_user
    
    # HR Specialist
    hr_user = SSOUser(
        user_id="hr-001",
        email="hr@company.com",
        roles=["hr_specialist"]
    )
    token = current_sso_user.set(hr_user)
    try:
        # Search HRM domain
        res = search_enterprise_knowledge("chấm công", system="HRM")
        assert len(res) > 0
        assert res[0]["article_id"] == "HRM-KB-101"

        # Get system manual for HRM
        manual_res = get_system_manual("HRM-KB-101")
        assert manual_res["status"] == "success"
        assert manual_res["article"]["id"] == "HRM-KB-101"
    finally:
        current_sso_user.reset(token)


def test_search_is_truncated_flag():
    store = KnowledgeStore()
    # ERP-KB-001 has content ~450 chars (< 1200 chars) -> is_truncated must be False
    results = store.search(query="purchase order sap", system="ERP")
    assert len(results) > 0
    assert results[0].is_truncated is False

    # Insert a long article > 1200 chars to verify truncation flag
    from agent_core.tools.enterprise_rag_mcp.rag_models import KnowledgeArticle
    long_article = KnowledgeArticle(
        id="ERP-KB-LONG",
        system="ERP",
        title="Long ERP Troubleshooting Guide",
        category="ERP Testing",
        content="A" * 1500,
        keywords=["long", "erp", "troubleshooting", "guide"],
        source_uri="docs/long.md",
        owner="erp@company.com",
    )
    store.articles.append(long_article)
    long_results = store.search(query="long erp troubleshooting", system="ERP")
    assert any(r.article_id == "ERP-KB-LONG" and r.is_truncated is True for r in long_results)

    # Test via main search_enterprise_knowledge tool
    mcp_res = search_enterprise_knowledge("purchase order sap", system="ERP")
    assert len(mcp_res) > 0
    assert mcp_res[0]["is_truncated"] is False

def test_draft_email_response():
    email = draft_email_response(
        user_name="Nguyễn Văn A",
        ticket_id="TICK-12345",
        issue_summary="Lỗi phân quyền SAP PO",
        solution_steps="1. Đã gán Role Z_PROC_PURCHASER.\n2. Vui lòng đăng xuất và đăng nhập lại SAP."
    )
    assert email["status"] == "success"
    assert "TICK-12345" in email["subject"]
    assert "Nguyễn Văn A" in email["body"]
    assert "Z_PROC_PURCHASER" in email["body"]


def test_search_enterprise_knowledge_invalid_system_boundary_validation():
    """Verify that specifying an unknown system returns a friendly validation error."""
    res = search_enterprise_knowledge("quy trình", system="UNKNOWN_SYSTEM")
    assert len(res) == 1
    assert res[0]["article_id"] == "INVALID-SYSTEM"
    assert "không hợp lệ" in res[0]["snippet"]
    assert "ERP" in res[0]["snippet"]


def test_mcp_tool_handles_knowledge_store_unavailable(monkeypatch):
    """Verify that MCP tool catches KnowledgeStoreUnavailableError and returns friendly error."""
    from agent_core.tools.enterprise_rag_mcp.knowledge_store import KnowledgeStoreUnavailableError
    from unittest.mock import MagicMock
    import agent_core.tools.enterprise_rag_mcp.main as main_module

    mock_store = MagicMock()
    mock_store.search.side_effect = KnowledgeStoreUnavailableError("BigQuery down")
    mock_store.get_article_by_id.side_effect = KnowledgeStoreUnavailableError("BigQuery down")
    monkeypatch.setattr(main_module, "store", mock_store)

    search_res = search_enterprise_knowledge("lỗi hệ thống", system="ERP")
    assert len(search_res) == 1
    assert search_res[0]["article_id"] == "STORE-UNAVAILABLE"
    assert "Tạm thời Gián đoạn" in search_res[0]["title"]

    manual_res = get_system_manual("ERP-KB-001")
    assert manual_res["status"] == "error"
    assert manual_res["error_code"] == "KNOWLEDGE_STORE_UNAVAILABLE"

