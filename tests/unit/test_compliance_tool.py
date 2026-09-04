import pytest
from agent_core.tools.compliance_tool import review_it_contract_sla, prescreen_contract_keywords
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
    res = prescreen_contract_keywords(contract_sample, vendor_name="Cloud SaaS Provider")
    assert res["status"] == "success"
    assert "99.95% Uptime" in res["uptime_commitments"]
    assert any("30 phút" in m for m in res["mttr_commitments"])
    assert any("4 giờ" in m for m in res["mttr_commitments"])
    assert res["compliance_checklist"]["NDA_CONFIDENTIALITY"] is True
    assert res["compliance_checklist"]["DPA_DATA_PROTECTION"] is True
    assert res["compliance_checklist"]["SERVICE_CREDITS_PENALTY"] is True
    assert res["compliance_checklist"]["DATA_BREACH_NOTIFICATION"] is True
    assert res["confidence_level"] == "HIGH"
    assert len(res["structured_findings"]) >= 4


def test_breach_notification_not_triggered_by_generic_24h():
    """
    Spec condition: Text contains 'SLA phản hồi trong 24h' but NO breach notification clause
    -> must NOT report DATA_BREACH_NOTIFICATION: true.
    """
    contract_text = """
    HỢP ĐỒNG DỊCH VỤ IT:
    - SLA phản hồi trong 24h đối với mọi yêu cầu hỗ trợ tiêu chuẩn.
    - Thời gian khắc phục trong vòng 48h.
    - Bảo mật thông tin: Cam kết giữ bí mật thông tin kinh doanh.
    """
    res = prescreen_contract_keywords(contract_text, vendor_name="Vendor Support")
    assert res["status"] == "success"
    # 24h was for SLA response, not breach notification
    assert res["compliance_checklist"]["DATA_BREACH_NOTIFICATION"] is False
    # No finding of category DATA_BREACH_NOTIFICATION
    assert not any(f["category"] == "DATA_BREACH_NOTIFICATION" for f in res["structured_findings"])


def test_structured_findings_contain_verified_citations():
    """
    Spec condition: Every item in structured findings must have quote and char_offset pointing to real text.
    """
    contract_text = """
    QUY ĐỊNH DỊCH VỤ:
    - Cam kết 99.9% uptime toàn hệ thống.
    - Thời gian phản hồi: 2 giờ.
    - Thỏa thuận bảo mật thông tin nội bộ.
    """
    res = prescreen_contract_keywords(contract_text, vendor_name="Vendor Alpha")
    assert res["status"] == "success"
    assert len(res["structured_findings"]) > 0

    for finding in res["structured_findings"]:
        if finding["status"] == "compliant":
            quote = finding["quote"]
            offset = finding["char_offset"]
            assert offset is not None and offset >= 0
            assert quote != ""
            # Verify quote is actually present in contract_text at/near that offset
            assert contract_text[offset:offset + len(quote)].lower() == quote.lower()


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
