import pytest
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

# Pins it-helpdesk pack because tests assert sample IT Helpdesk SLA obligations (SAP Enterprise, etc.)
pytestmark = pytest.mark.usefixtures("pinned_it_helpdesk_pack")

from agent_core.tools.enterprise_rag_mcp.rag_models import Obligation
from agent_core.tools.obligations_store import (
    BaseObligationsStore,
    InMemoryObligationsStore,
    BigQueryObligationsStore,
    get_obligations_store,
    load_sample_obligations,
)
from agent_core.tools.enterprise_rag_mcp.knowledge_store import KnowledgeStoreUnavailableError
from agent_core.tools.compliance_tool import (
    get_obligation,
    list_contract_obligations,
    review_it_contract_sla,
)
from agent_core.tools.enterprise_rag_mcp.main import get_obligation as mcp_get_obligation
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user


class TestObligationModel:
    def test_obligation_model_valid(self):
        ob = Obligation(
            obligation_id="OBL-TEST-001",
            source_id="CONTRACT-TEST",
            source_title="Test Agreement",
            authority="Legal Counsel",
            article="Section 1.1",
            description="Test obligation commitment",
            severity="high",
            applies_to="vendor",
            date_added="2024-01-01",
            date_effective="2024-01-01",
            status="active",
        )
        assert ob.obligation_id == "OBL-TEST-001"
        assert ob.severity == "high"
        assert ob.status == "active"

    def test_obligation_model_extra_forbid(self):
        with pytest.raises(ValidationError):
            Obligation(
                obligation_id="OBL-TEST-002",
                source_id="CONTRACT-TEST",
                source_title="Test Agreement",
                authority="Legal Counsel",
                description="Test obligation commitment",
                severity="high",
                applies_to="vendor",
                date_added="2024-01-01",
                date_effective="2024-01-01",
                unexpected_field="disallowed",
            )


class TestInMemoryObligationsStore:
    def test_initial_obligations_seeded(self):
        store = InMemoryObligationsStore()
        assert len(store._obligations) >= 15
        assert store.get_obligation("OBL-SAP-001") is not None
        assert store.get_obligation("OBL-SAP-001").severity == "critical"

    def test_get_obligation_case_insensitive_and_whitespace(self):
        store = InMemoryObligationsStore()
        ob = store.get_obligation("  obl-sap-003  ")
        assert ob is not None
        assert ob.obligation_id == "OBL-SAP-003"
        assert "4 giờ" in ob.description

    def test_get_obligation_not_found(self):
        store = InMemoryObligationsStore()
        assert store.get_obligation("OBL-NONEXISTENT-999") is None
        assert store.get_obligation("") is None

    def test_list_obligations_by_source_id(self):
        store = InMemoryObligationsStore()
        sap_obs = store.list_obligations(source_id="CONTRACT-SAP-ENTERPRISE-2024")
        assert len(sap_obs) == 7
        assert all(ob.source_id == "CONTRACT-SAP-ENTERPRISE-2024" for ob in sap_obs)

    def test_list_obligations_by_severity_and_applies_to(self):
        store = InMemoryObligationsStore()
        critical_vendor_obs = store.list_obligations(severity="critical", applies_to="vendor")
        assert len(critical_vendor_obs) >= 3
        for ob in critical_vendor_obs:
            assert ob.severity == "critical"
            assert ob.applies_to == "vendor"


class TestBigQueryObligationsStore:
    def test_bigquery_obligations_get_success(self):
        bq_store = BigQueryObligationsStore(project_id="test-proj")
        mock_client = MagicMock()
        mock_job = MagicMock()
        
        mock_row = MagicMock()
        mock_row.obligation_id = "OBL-SAP-001"
        mock_row.source_id = "CONTRACT-SAP-ENTERPRISE-2024"
        mock_row.source_title = "SAP Enterprise Support Agreement"
        mock_row.authority = "VP of IT"
        mock_row.article = "Section 3.1"
        mock_row.description = "Cam kết Uptime hệ thống tối thiểu 99.95% mỗi tháng theo lịch 24/7."
        mock_row.severity = "critical"
        mock_row.applies_to = "vendor"
        mock_row.date_added = "2024-01-01"
        mock_row.date_effective = "2024-01-01"
        mock_row.date_expires = None
        mock_row.status = "active"
        mock_row.source_document_path = "docs/contracts/sap.pdf"

        mock_job.result.return_value = [mock_row]
        mock_client.query.return_value = mock_job
        bq_store.bq_client = mock_client

        ob = bq_store.get_obligation("OBL-SAP-001")
        assert ob is not None
        assert ob.obligation_id == "OBL-SAP-001"
        assert ob.severity == "critical"

        # Verify query parameters were used (SQL injection prevention)
        call_kwargs = mock_client.query.call_args[1]
        assert "job_config" in call_kwargs
        params = call_kwargs["job_config"].query_parameters
        assert len(params) == 1
        assert params[0].name == "obligation_id"
        assert params[0].value == "OBL-SAP-001"

    def test_bigquery_obligations_cancellation_on_timeout(self):
        bq_store = BigQueryObligationsStore(project_id="test-proj", timeout_seconds=0.1)
        mock_client = MagicMock()
        mock_job = MagicMock()
        mock_job.result.side_effect = TimeoutError("Query timed out after 100ms")
        mock_client.query.return_value = mock_job
        bq_store.bq_client = mock_client

        with pytest.raises(KnowledgeStoreUnavailableError):
            bq_store.get_obligation("OBL-SAP-001")

        mock_job.cancel.assert_called_once()


class TestObligationsRBACAndTools:
    def test_get_obligation_authorized_roles(self, monkeypatch):
        monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
        for role in ["compliance_officer", "legal_counsel", "it_admin", "sys_admin"]:
            user = SSOUser(user_id="auditor-1", email="auditor@enterprise.local", roles=[role])
            token = current_sso_user.set(user)
            try:
                res = get_obligation("OBL-SAP-001")
                assert res["status"] == "success"
                assert res["obligation_id"] == "OBL-SAP-001"
                assert res["severity"] == "critical"
            finally:
                current_sso_user.reset(token)

    def test_get_obligation_unauthorized_role(self, monkeypatch):
        monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
        user = SSOUser(user_id="emp-1", email="user@enterprise.local", roles=["employee"])
        token = current_sso_user.set(user)
        try:
            res = get_obligation("OBL-SAP-001")
            assert res["status"] == "forbidden"
            assert res["error"] == "Access Denied"
        finally:
            current_sso_user.reset(token)

    def test_list_contract_obligations_authorized(self, monkeypatch):
        monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
        user = SSOUser(user_id="comp-1", email="compliance@enterprise.local", roles=["compliance_officer"])
        token = current_sso_user.set(user)
        try:
            res = list_contract_obligations(source_id="CONTRACT-SAP-ENTERPRISE-2024")
            assert res["status"] == "success"
            assert res["count"] == 7
            assert len(res["obligations"]) == 7
        finally:
            current_sso_user.reset(token)

    def test_list_contract_obligations_unauthorized(self, monkeypatch):
        monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
        user = SSOUser(user_id="emp-1", email="user@enterprise.local", roles=["employee"])
        token = current_sso_user.set(user)
        try:
            res = list_contract_obligations()
            assert res["status"] == "forbidden"
            assert res["error"] == "Access Denied"
        finally:
            current_sso_user.reset(token)

    def test_mcp_get_obligation_authorized_and_denied(self, monkeypatch):
        monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
        user_auth = SSOUser(user_id="comp-1", email="compliance@enterprise.local", roles=["compliance_officer"])
        token_auth = current_sso_user.set(user_auth)
        try:
            res = mcp_get_obligation("OBL-SAP-001")
            assert res["status"] == "success"
            assert res["obligation_id"] == "OBL-SAP-001"
        finally:
            current_sso_user.reset(token_auth)

        user_emp = SSOUser(user_id="emp-1", email="emp@enterprise.local", roles=["employee"])
        token_emp = current_sso_user.set(user_emp)
        try:
            res_denied = mcp_get_obligation("OBL-SAP-001")
            assert res_denied["status"] == "forbidden"
        finally:
            current_sso_user.reset(token_emp)

    def test_review_it_contract_sla_integrates_obligations(self, monkeypatch):
        monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
        user = SSOUser(user_id="legal-1", email="legal@enterprise.local", roles=["legal_counsel"])
        token = current_sso_user.set(user)
        try:
            contract_text = """
            Hợp đồng dịch vụ phần mềm SAP Enterprise.
            Nhà cung cấp cam kết Uptime đạt 99.95% mỗi tháng.
            Thời gian phản hồi sự cố khẩn cấp trong vòng 30 phút và MTTR 4 giờ.
            Nếu không đạt, áp dụng Service Credits bồi thường 10%.
            """
            result = review_it_contract_sla(
                contract_text=contract_text,
                vendor_name="SAP",
            )
            assert result["status"] == "success"
            assert "registered_contract_obligations" in result
            assert len(result["registered_contract_obligations"]) > 0
            assert any(ob["obligation_id"] == "OBL-SAP-001" for ob in result["registered_contract_obligations"])
        finally:
            current_sso_user.reset(token)
