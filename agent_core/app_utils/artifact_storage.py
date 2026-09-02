import os
import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger("enterprise_artifact_storage")

MAX_ARTIFACT_BYTES = int(os.getenv("MAX_ARTIFACT_BYTES", str(5 * 1024 * 1024)))  # 5 MB
GCS_URI_PATTERN = re.compile(r"^gs://([a-z0-9._-]+)/([A-Za-z0-9._\-/]+)$")


def read_gcs_artifact(uri: str, allowed_bucket: str) -> str:
    """Reads GCS object content safely using Google Cloud Storage client with strict size limit."""
    from google.cloud import storage
    
    match = GCS_URI_PATTERN.match(uri)
    if not match:
        raise ValueError(f"Định dạng URI GCS không hợp lệ: {uri}")
    
    bucket_name, blob_name = match.groups()
    if allowed_bucket and bucket_name != allowed_bucket:
        raise PermissionError(f"Bucket '{bucket_name}' không nằm trong danh sách bucket được cấp phép: '{allowed_bucket}'.")
    
    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    
    data = blob.download_as_bytes(start_byte=0, end_byte=MAX_ARTIFACT_BYTES)
    return data.decode("utf-8", errors="replace")


def resolve_artifact_content(
    ref: Optional[str] = None,
    raw_text: Optional[str] = None,
    resource_label: str = "tài liệu",
) -> Tuple[Optional[str], Optional[dict]]:
    """
    Safely resolves artifact text exclusively from direct raw_text or a validated gs:// Cloud Storage URI.
    Strict Whitelist: Local filesystem access is entirely forbidden (no open() calls).
    
    Returns (content, error_dict). If error_dict is not None, caller must return error_dict.
    """
    if raw_text and raw_text.strip():
        return raw_text, None

    if not ref or not ref.strip():
        return None, {
            "status": "error",
            "error": "Missing Input",
            "error_code": "MISSING_INPUT",
            "message": f"Vui lòng cung cấp nội dung trực tiếp (raw text) hoặc đường dẫn tham chiếu Cloud Storage hợp lệ ({resource_label}_ref).",
        }

    clean_ref = ref.strip()

    # Handle Cloud Storage gs:// URI with strict Whitelist validation
    if clean_ref.startswith("gs://"):
        match = GCS_URI_PATTERN.match(clean_ref)
        if not match:
            logger.warning("Invalid GCS URI format: %s", clean_ref)
            return None, {
                "status": "error",
                "error": "Invalid Artifact Reference",
                "error_code": "INVALID_ARTIFACT_REF",
                "message": f"Định dạng URI Cloud Storage không hợp lệ: '{clean_ref}'. Cần tuân theo định dạng 'gs://<bucket>/<path>'.",
            }

        bucket_name, _ = match.groups()
        allowed_bucket = os.getenv("ALLOWED_ARTIFACT_BUCKET", "").strip()
        is_prod = os.getenv("ENVIRONMENT", "development").lower() == "production" or bool(os.getenv("K_SERVICE"))

        if is_prod:
            if not allowed_bucket:
                logger.error("ALLOWED_ARTIFACT_BUCKET is not configured in production environment.")
                return None, {
                    "status": "error",
                    "error": "Configuration Error",
                    "error_code": "ARTIFACT_BUCKET_NOT_CONFIGURED",
                    "message": "Hệ thống chưa cấu hình ALLOWED_ARTIFACT_BUCKET. Vui lòng truyền nội dung trực tiếp qua text.",
                }
            if bucket_name != allowed_bucket:
                logger.warning("Unauthorized GCS bucket requested: %s (allowed: %s)", bucket_name, allowed_bucket)
                return None, {
                    "status": "error",
                    "error": "Forbidden Bucket",
                    "error_code": "FORBIDDEN_BUCKET",
                    "message": f"Bucket '{bucket_name}' không được phép truy cập. Chỉ chấp nhận bucket '{allowed_bucket}'.",
                }
        else:
            if allowed_bucket and bucket_name != allowed_bucket:
                logger.warning("Bucket mismatch in non-production: %s != %s", bucket_name, allowed_bucket)
                return None, {
                    "status": "error",
                    "error": "Forbidden Bucket",
                    "error_code": "FORBIDDEN_BUCKET",
                    "message": f"Bucket '{bucket_name}' không nằm trong danh sách bucket được phép ('{allowed_bucket}').",
                }
            allowed_bucket = allowed_bucket or bucket_name

        try:
            content = read_gcs_artifact(clean_ref, allowed_bucket)
            return content, None
        except Exception as e:
            logger.error("Failed to read GCS artifact '%s': %s", clean_ref, e)
            return None, {
                "status": "error",
                "error": "Artifact Read Failure",
                "error_code": "ARTIFACT_READ_ERROR",
                "message": f"Không thể đọc {resource_label} từ Cloud Storage: {e}",
            }

    # All local filesystem access (/proc, /etc, relative paths, local filenames) is strictly rejected
    logger.warning("Blocked non-GCS artifact access attempt: %s", clean_ref)
    return None, {
        "status": "error",
        "error": "Invalid Artifact Reference",
        "error_code": "INVALID_ARTIFACT_REF",
        "message": "Truy cập hệ thống tệp tin cục bộ bị nghiêm cấm theo chính sách Zero-Trust. Chỉ chấp nhận đường dẫn Cloud Storage ('gs://...') hoặc nội dung văn bản trực tiếp.",
    }
