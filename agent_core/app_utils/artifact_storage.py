import os
import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger("enterprise_artifact_storage")

MAX_ARTIFACT_BYTES = int(os.getenv("MAX_ARTIFACT_BYTES", str(5 * 1024 * 1024)))  # 5 MB


def read_gcs_artifact(uri: str, allowed_bucket: str) -> str:
    """Reads GCS object content safely using Google Cloud Storage client with size limit."""
    from google.cloud import storage
    
    # Strip gs:// prefix
    path_without_prefix = uri[5:]
    parts = path_without_prefix.split("/", 1)
    if len(parts) != 2:
        raise ValueError(f"Định dạng URI GCS không hợp lệ: {uri}")
    
    bucket_name, blob_name = parts
    if bucket_name != allowed_bucket:
        raise PermissionError(f"Bucket '{bucket_name}' không nằm trong danh sách bucket được cấp phép: '{allowed_bucket}'.")
    
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    
    # Read up to MAX_ARTIFACT_BYTES
    data = blob.download_as_bytes(start_byte=0, end_byte=MAX_ARTIFACT_BYTES)
    return data.decode("utf-8", errors="replace")


def resolve_artifact_content(
    ref: Optional[str] = None,
    raw_text: Optional[str] = None,
    resource_label: str = "tài liệu",
) -> Tuple[Optional[str], Optional[dict]]:
    """
    Safely resolves artifact text from either direct raw_text, a local test file, or a validated gs:// URI.
    Strictly forbids sensitive system access (/proc, /etc, /sys, traversal ..).
    
    Returns (content, error_dict). If error_dict is not None, caller must return error_dict.
    """
    if raw_text and raw_text.strip():
        return raw_text, None

    if not ref or not ref.strip():
        return None, {
            "status": "error",
            "error": "Missing Input",
            "error_code": "MISSING_INPUT",
            "message": f"Vui lòng cung cấp nội dung trực tiếp (raw text) hoặc đường dẫn tham chiếu hợp lệ ({resource_label}_ref).",
        }

    clean_ref = ref.strip()

    # If ref contains newlines, it's inline text passed directly
    if "\n" in clean_ref:
        return clean_ref, None

    # Reject null byte and path traversal attempts explicitly
    if "\x00" in clean_ref or ".." in clean_ref:
        logger.warning("Blocked attempt to access local file or path traversal: %s", clean_ref)
        return None, {
            "status": "error",
            "error": "Invalid Artifact Reference",
            "error_code": "INVALID_ARTIFACT_REF",
            "message": f"Truy cập filesystem cục bộ bị nghiêm cấm. Phát hiện hành vi path traversal.",
        }

    # Explicitly block sensitive operating system directories
    sensitive_prefixes = ("/etc", "/proc", "/sys", "/dev", "/root", "/boot", "/bin", "/sbin", "/lib")
    if any(clean_ref.startswith(p) for p in sensitive_prefixes):
        logger.warning("Blocked attempt to access sensitive OS path: %s", clean_ref)
        return None, {
            "status": "error",
            "error": "Access Denied",
            "error_code": "FORBIDDEN_SYSTEM_PATH",
            "message": f"Truy cập các tệp tin hệ thống nhạy cảm bị nghiêm cấm theo chính sách an ninh.",
        }

    # Handle Cloud Storage gs:// URI
    if clean_ref.startswith("gs://"):
        allowed_bucket = os.getenv("ALLOWED_ARTIFACT_BUCKET", "").strip()
        if not allowed_bucket:
            # If no bucket is configured, in non-production allow test bucket
            is_prod = os.getenv("ENVIRONMENT", "development").lower() == "production" or bool(os.getenv("K_SERVICE"))
            if is_prod:
                logger.error("ALLOWED_ARTIFACT_BUCKET is not configured in production.")
                return None, {
                    "status": "error",
                    "error": "Configuration Error",
                    "error_code": "ARTIFACT_BUCKET_NOT_CONFIGURED",
                    "message": "Hệ thống chưa cấu hình ALLOWED_ARTIFACT_BUCKET. Vui lòng truyền nội dung trực tiếp qua text.",
                }
            else:
                # Extract bucket from URI in dev/test
                allowed_bucket = clean_ref[5:].split("/", 1)[0]

        pattern = rf"^gs://{re.escape(allowed_bucket)}/[A-Za-z0-9._\-/]+$"
        if not re.match(pattern, clean_ref):
            logger.warning("URI does not match allowed bucket pattern '%s': %s", pattern, clean_ref)
            return None, {
                "status": "error",
                "error": "Invalid Artifact Reference",
                "error_code": "INVALID_ARTIFACT_REF",
                "message": f"Đường dẫn tham chiếu không hợp lệ hoặc không thuộc bucket được phép ('gs://{allowed_bucket}/...').",
            }

        try:
            content = read_gcs_artifact(clean_ref, allowed_bucket)
            return content, None
        except Exception as e:
            logger.error("Failed to read GCS artifact '%s': %s", clean_ref, e)
            return None, {
                "status": "error",
                "error": "Artifact Read Failure",
                "error_code": "ARTIFACT_READ_ERROR",
                "message": f"Không thể đọc {resource_label} từ đường dẫn Cloud Storage: {e}",
            }

    # Handle local file reference (for unit tests / dev / temp artifacts)
    if os.path.exists(clean_ref) and os.path.isfile(clean_ref):
        try:
            file_size = os.path.getsize(clean_ref)
            if file_size > MAX_ARTIFACT_BYTES:
                return None, {
                    "status": "error",
                    "error": "File Too Large",
                    "error_code": "FILE_TOO_LARGE",
                    "message": f"Kích thước tệp ({file_size} bytes) vượt quá giới hạn cho phép ({MAX_ARTIFACT_BYTES} bytes).",
                }
            with open(clean_ref, "r", encoding="utf-8", errors="replace") as f:
                return f.read(), None
        except Exception as e:
            logger.error("Failed to read local file '%s': %s", clean_ref, e)
            return None, {
                "status": "error",
                "error": "File Read Failure",
                "error_code": "FILE_READ_ERROR",
                "message": f"Không thể đọc {resource_label}: {e}",
            }

    # If it's plain text without newlines (e.g. single line log or short contract snippet)
    return clean_ref, None
