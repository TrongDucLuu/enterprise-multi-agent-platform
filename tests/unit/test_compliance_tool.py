import pytest
from agent_core.tools.compliance_tool import review_it_contract_sla
from agent_core.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture(autouse=True)
def default_authorized_user():
    """Sets a default compliance officer user in context for compliance tool tests."""
    user = SSOUser(
        user_id="compliance-officer-01",
        email="compliance@company.com",
        roles=["employee", "compliance_officer", "it_admin"],
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)


def test_review_contract_with_full_sla():
    contract_sample = """
    MASTER SERVICES AGREEMENT
    1. Cam kết chất lượng dịch vụ: Nhà cung cấp cam kết 99.95% uptime hàng tháng.
    2. Thời gian phản hồi sự cố khẩn cấp (P1) trong vòng 30 phút, giải quyết trong vòng 4 giờ.
    3. Điều khoản bồi thường: Nếu uptime dưới 99.9%, khách hàng nhận Service Credit 10% phí dịch vụ.
    4. Bảo mật thông tin: Tuân thủ thỏa thuận Non-Disclosure Agreement (NDA) và Data Protection Addendum (DPA).
    5. Thông báo sự cố: Nhà cung cấp có trách nhiệm thông báo vi phạm dữ liệu trong vòng 24h.
    6. Quyền kiểm toán: Khách hàng có quyền audit an toàn thông tin hàng năm.
    """
    res = review_it_contract_sla(contract_sample, vendor_name="Cloud SaaS Provider")
    assert res["status"] == "success"
    assert "99.95% Uptime" in res["uptime_commitments"]
    assert any("30 phút" in m for m in res["mttr_commitments"])
    assert any("4 giờ" in m for m in res["mttr_commitments"])
    assert res["compliance_checklist"]["NDA_CONFIDENTIALITY"] is True
    assert res["compliance_checklist"]["DPA_DATA_PROTECTION"] is True
    assert res["compliance_checklist"]["SERVICE_CREDITS_PENALTY"] is True
    assert len(res["identified_legal_risks"]) == 1
    assert "đáp ứng đầy đủ" in res["identified_legal_risks"][0]


def test_review_contract_prefix_syntax():
    contract_prefix = """
    SERVICE SPECIFICATION:
    - Target Uptime: 99.9%
    - Response Time: 2 hours
    - Resolve Time: 12 hours
    - Security: Confidentiality agreement applies.
    """
    res = review_it_contract_sla(contract_prefix, vendor_name="Prefix Provider")
    assert res["status"] == "success"
    assert "99.9% Uptime" in res["uptime_commitments"]
    assert any("2 hours" in m for m in res["mttr_commitments"])
    assert any("12 hours" in m for m in res["mttr_commitments"])


def test_review_contract_with_missing_penalties():
    poor_contract = """
    Cung cấp phần mềm quản lý kho.
    Hệ thống duy trì trạng thái hoạt động bình thường và hỗ trợ bảo mật cơ bản.
    """
    res = review_it_contract_sla(poor_contract, vendor_name="Vendor X")
    assert res["status"] == "success"
    assert res["compliance_checklist"]["SERVICE_CREDITS_PENALTY"] is False
    assert any("RỦI RO CAO" in r for r in res["identified_legal_risks"])


def test_review_contract_rbac_denied(monkeypatch):
    monkeypatch.setattr("agent_core.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
    
    # Override context with unauthorized employee role only
    user = SSOUser(
        user_id="emp-1",
        email="emp@company.com",
        roles=["employee"]
    )
    token = current_sso_user.set(user)
    try:
        res = review_it_contract_sla("Some contract", vendor_name="Vendor Y")
        assert res["status"] == "forbidden"
        assert "không đủ" in res["message"]
    finally:
        current_sso_user.reset(token)
