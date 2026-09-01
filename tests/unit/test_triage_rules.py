import pytest
from agent_core.app_utils.triage_rules import classify_intent_fast_path


def test_triage_fast_path_l3():
    q = "Hệ thống sập với stack trace NullPointerException và OutOfMemoryError"
    result = classify_intent_fast_path(q)
    assert result is not None
    assert result["tier"] == "L3"
    assert result["target_agent"] == "l3_deep_diagnostics_agent"
    assert result["confidence"] >= 0.95


def test_triage_fast_path_l2_erp():
    q = "Lỗi tạo purchase order ME21N trên hệ thống SAP"
    result = classify_intent_fast_path(q)
    assert result is not None
    assert result["tier"] == "L2"
    assert result["target_agent"] == "l2_enterprise_rag_agent"


def test_triage_fast_path_l2_hrm():
    q = "Lỗi đồng bộ bảng lương payroll trên Workday"
    result = classify_intent_fast_path(q)
    assert result is not None
    assert result["tier"] == "L2"
    assert result["target_agent"] == "l2_enterprise_rag_agent"


def test_triage_fast_path_l1():
    q = "Tôi bị quên mật khẩu và muốn reset password tài khoản"
    result = classify_intent_fast_path(q)
    assert result is not None
    assert result["tier"] == "L1"
    assert result["target_agent"] == "l1_selfservice_agent"


def test_triage_fast_path_ambiguous_returns_none():
    q = "Xin chào tôi muốn hỏi một chút thông tin"
    result = classify_intent_fast_path(q)
    assert result is None
