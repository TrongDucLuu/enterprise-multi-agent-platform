import pytest
from it_helpdesk_agent.tools.log_analyzer import analyze_system_logs_for_rca

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
