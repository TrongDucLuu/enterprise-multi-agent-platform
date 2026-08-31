import os
import re
from typing import Optional
from it_helpdesk_agent.app_utils.sso_auth import require_role

def analyze_system_logs_for_rca(
    log_ref: Optional[str] = None,
    raw_logs: Optional[str] = None,
    system_name: str = "Core System",
    incident_description: Optional[str] = None
) -> dict:
    """
    Parses application logs, stack traces, and syslog lines to identify root cause indicators.
    Detects critical error patterns, affected components, and recommended mitigation steps.
    Supports reference-based ingestion (log_ref) for large log files without saturating model context.
    Protected by RBAC: requires it_admin, sys_admin, devops_engineer, or lead_engineer.
    """
    # 1. RBAC Authorization Gate
    is_allowed, error_msg = require_role(["it_admin", "sys_admin", "devops_engineer", "lead_engineer"])
    if not is_allowed:
        return {
            "status": "forbidden",
            "error": "Access Denied",
            "message": error_msg,
            "system": system_name,
        }

    # 2. Resolve log content from reference (log_ref) or direct text (raw_logs)
    content: Optional[str] = None
    if log_ref:
        clean_path = log_ref.replace("file://", "")
        if os.path.exists(clean_path) and os.path.isfile(clean_path):
            try:
                with open(clean_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except Exception as e:
                return {
                    "status": "error",
                    "error": "File Read Failure",
                    "message": f"Không thể đọc file log từ tham chiếu '{log_ref}': {e}",
                    "system": system_name,
                }
        elif "\n" in log_ref or len(log_ref) > 260:
            # Fallback if raw logs were passed positionally as first argument
            content = log_ref
        else:
            return {
                "status": "error",
                "error": "Log Reference Not Found",
                "message": f"Tham chiếu log '{log_ref}' không tồn tại trên hệ thống lưu trữ.",
                "system": system_name,
            }
    elif raw_logs:
        content = raw_logs
    else:
        return {
            "status": "error",
            "error": "Missing Input",
            "message": "Vui lòng cung cấp tham chiếu file log (log_ref) hoặc chuỗi log (raw_logs).",
            "system": system_name,
        }

    lines = content.strip().split("\n")
    error_lines = []
    fatal_count = 0
    error_count = 0
    warning_count = 0

    known_patterns = {
        "OUT_OF_MEMORY": r"(OutOfMemoryError|java\.lang\.OutOfMemoryError|memory cgroup out of memory|OOMKilled|exit code 137)",
        "DB_CONNECTION_EXHAUSTED": r"(Connection pool exhausted|Too many connections|Deadlock detected|HikariPool.*Connection is not available)",
        "NETWORK_TIMEOUT": r"(Read timed out|ConnectTimeoutException|Connection refused|ETIMEDOUT|504 Gateway Time-out)",
        "AUTH_SECURITY_FAILURE": r"(Unauthorized|401 Access Denied|403 Forbidden|JWT expired|Invalid signature|Certificate has expired)",
        "DATA_CORRUPTION_NULL": r"(NullPointerException|KeyError|TypeError: Cannot read property|ConstraintViolationException)",
        "DISK_IO_FAILURE": r"(No space left on device|Disk full|I/O error|Read-only file system)",
    }

    detected_anomalies: list[str] = []

    for line in lines:
        upper = line.upper()
        if "FATAL" in upper or "CRITICAL" in upper:
            fatal_count += 1
            error_lines.append(line)
        elif "ERROR" in upper or "EXCEPTION" in upper:
            error_count += 1
            error_lines.append(line)
        elif "WARN" in upper:
            warning_count += 1

        for pattern_name, regex in known_patterns.items():
            if re.search(regex, line, re.IGNORECASE) and pattern_name not in detected_anomalies:
                detected_anomalies.append(pattern_name)

    # Formulate Root Cause Hypotheses for all anomaly types
    hypotheses = []
    if "OUT_OF_MEMORY" in detected_anomalies:
        hypotheses.append("Tiến trình bị hạ gục bởi Linux OOM-Killer hoặc tràn Heap memory do rò rỉ bộ nhớ (Memory Leak) hoặc tải đột biến.")
    if "DB_CONNECTION_EXHAUSTED" in detected_anomalies:
        hypotheses.append("Cạn kiệt Connection Pool tới Database; các truy vấn chậm (slow queries) giữ connection quá lâu gây tắc nghẽn toàn hệ thống.")
    if "NETWORK_TIMEOUT" in detected_anomalies:
        hypotheses.append("Đứt gãy mạng hoặc service downstream phản hồi quá thời gian quy định (Timeout / Network Partition).")
    if "AUTH_SECURITY_FAILURE" in detected_anomalies:
        hypotheses.append("Chứng chỉ SSL/TLS hoặc Token xác thực dịch vụ nội bộ đã hết hạn hoặc secret key bị sai lệch.")
    if "DISK_IO_FAILURE" in detected_anomalies:
        hypotheses.append("Phân vùng ổ đĩa bị đầy (Disk Full / No space left) hoặc lỗi phần cứng I/O khiến hệ thống chuyển sang chế độ Read-Only.")
    if "DATA_CORRUPTION_NULL" in detected_anomalies:
        hypotheses.append("Lỗi logic ứng dụng do dữ liệu đầu vào bị null/corrupt hoặc payload vi phạm ràng buộc schema/kiểu dữ liệu.")

    if not hypotheses:
        hypotheses.append("Cần kiểm tra sâu hơn log tầng kernel hoặc APM traces do lỗi xuất phát từ logic application bất thường.")

    # Guardrails: Determine confidence level based on empirical log signals
    if fatal_count > 0 or (len(detected_anomalies) > 0 and error_count >= 2):
        confidence_level = "HIGH"
    elif error_count > 0 or len(detected_anomalies) > 0:
        confidence_level = "MEDIUM"
    else:
        confidence_level = "LOW"

    return {
        "status": "success",
        "system": system_name,
        "metrics": {
            "total_lines_analyzed": len(lines),
            "fatal_count": fatal_count,
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "detected_anomalies": detected_anomalies,
        "root_cause_hypotheses": hypotheses,
        "sample_error_traces": error_lines[:5],
        "incident_context": incident_description,
        # Mandatory P0 Output Guardrails
        "confidence_level": confidence_level,
        "requires_human_review": True,
        "disclaimer": (
            "Kết quả phân tích nguyên nhân gốc rễ (RCA) là giả thuyết chẩn đoán tự động hỗ trợ bởi AI, "
            "KHÔNG phải là kết luận điều tra sự cố chính thức. Bắt buộc cần có kỹ sư/quản trị viên hệ thống "
            "xác minh và phê duyệt trước khi áp dụng hành động can thiệp vào môi trường vận hành."
        ),
    }
