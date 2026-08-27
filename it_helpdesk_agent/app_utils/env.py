import os
import logging
import google.auth
import vertexai
from google.cloud import secretmanager
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

def _fetch_secrets(project_id: str) -> dict[str, str]:
    """Fetch sensitive credentials from Secret Manager and return them as an in-memory dictionary."""
    secrets_to_fetch = [
        "HELPDESK_ADMIN_API_KEY",
        "ERP_INTEGRATION_TOKEN",
        "HRM_INTEGRATION_TOKEN",
        "CRM_INTEGRATION_TOKEN",
        "SSO_CLIENT_SECRET",
        "SSO_JWT_SECRET"
    ]
    fetched_secrets: dict[str, str] = {}

    # 1. Check local environment first (.env)
    for s in secrets_to_fetch:
        val = os.getenv(s)
        if val:
            fetched_secrets[s] = val

    # 2. If secrets missing and project_id exists, fetch from Secret Manager
    if len(fetched_secrets) < len(secrets_to_fetch) and project_id:
        try:
            client = secretmanager.SecretManagerServiceClient()
            for secret_id in secrets_to_fetch:
                if secret_id not in fetched_secrets:
                    name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
                    try:
                        response = client.access_secret_version(request={"name": name})
                        fetched_secrets[secret_id] = response.payload.data.decode("UTF-8")
                    except Exception as e:
                        logger.warning(f"Could not fetch secret '{secret_id}' from Secret Manager: {e}")
        except Exception as e:
            logger.warning(f"Could not initialize Secret Manager client: {e}")

    return fetched_secrets

def init_environment() -> tuple[str | None, str, str, dict[str, str]]:
    """Discovers environment configuration, initializes Vertex AI, and retrieves secrets."""
    load_dotenv()
    project_id = None
    try:
        _, project_id = google.auth.default()
    except Exception:
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")

    model_location = os.getenv("GOOGLE_CLOUD_LOCATION", "global")
    service_location = os.getenv("GOOGLE_CLOUD_REGION", "us-central1")

    secrets: dict[str, str] = {}
    if project_id:
        try:
            vertexai.init(project=project_id, location=service_location)
        except Exception as e:
            logger.warning(f"Vertex AI initialization warning: {e}")
        secrets = _fetch_secrets(project_id)

    return project_id, model_location, service_location, secrets
