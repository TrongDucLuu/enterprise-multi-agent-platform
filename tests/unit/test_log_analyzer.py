import pytest
from it_helpdesk_agent.tools.log_analyzer import analyze_system_logs_for_rca
from it_helpdesk_agent.app_utils.sso_auth import SSOUser, current_sso_user


@pytest.fixture(autouse=True)
def default_authorized_admin():
    """Sets a default IT admin user in context for log analyzer RCA tests."""
    user = SSOUser(
        user_id="sysadmin-01",
        email="sysadmin@company.com",
        roles=["employee", "it_admin", "sys_admin"],
    )
    token = current_sso_user.set(user)
    yield user
    current_sso_user.reset(token)


def test_analyze_logs_detects_oom():
    raw_logs = """
    2026-08-27T10:15:30.123Z INFO  App starting...
    2026-08-27T10:15:32.456Z ERROR java.lang.OutOfMemoryError: Java heap space
    2026-08-27T10:15:32.457Z FATAL Container terminated: exit code 137 (OOMKilled)
    """
    res = analyze_system_logs_for_rca(raw_logs, system_name="Payment Gateway")
    assert res["status"] == "success"
    assert "OUT_OF_MEMORY" in res["detected_anomalies"]
    assert res["metrics"]["fatal_count"] == 1
    assert res["metrics"]["error_count"] == 1
    assert any("OOM-Killer" in h or "Heap memory" in h for h in res["root_cause_hypotheses"])


def test_analyze_logs_detects_db_exhaustion():
    raw_logs = """
    2026-08-27T11:00:00Z WARN  Slow query detected on orders table (duration: 15420ms)
    2026-08-27T11:00:05Z ERROR HikariPool-1 - Connection is not available, request timed out after 30000ms. Connection pool exhausted.
    2026-08-27T11:00:06Z ERROR Deadlock detected when trying to acquire lock on table INVENTORY
    """
    res = analyze_system_logs_for_rca(raw_logs, system_name="E-Commerce API")
    assert res["status"] == "success"
    assert "DB_CONNECTION_EXHAUSTED" in res["detected_anomalies"]
    assert res["metrics"]["warning_count"] == 1
    assert res["metrics"]["error_count"] == 2


def test_analyze_logs_detects_disk_and_null_errors():
    raw_logs = """
    2026-08-27T12:00:01Z ERROR java.io.IOException: No space left on device. Disk full.
    2026-08-27T12:00:02Z ERROR TypeError: Cannot read property 'user_id' of null. NullPointerException.
    """
    res = analyze_system_logs_for_rca(raw_logs, system_name="Storage Service")
    assert res["status"] == "success"
    assert "DISK_IO_FAILURE" in res["detected_anomalies"]
    assert "DATA_CORRUPTION_NULL" in res["detected_anomalies"]
    assert any("Disk Full" in h or "Read-Only" in h for h in res["root_cause_hypotheses"])
    assert any("null/corrupt" in h for h in res["root_cause_hypotheses"])


def test_analyze_logs_rbac_denied(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)

    # Override context with unauthorized employee role only
    user = SSOUser(
        user_id="emp-2",
        email="regular.employee@company.com",
        roles=["employee"]
    )
    token = current_sso_user.set(user)
    try:
        res = analyze_system_logs_for_rca("ERROR some error", system_name="Core DB")
        assert res["status"] == "forbidden"
        assert "không đủ" in res["message"]
    finally:
        current_sso_user.reset(token)
