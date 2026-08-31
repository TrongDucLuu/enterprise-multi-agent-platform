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
        secrets = _fetch_secrets(project_id)
        try:
            vertexai.init(project=project_id, location=service_location)
        except Exception as e:
            logger.warning(f"Vertex AI initialization warning: {e}")
    return project_id, model_location, service_location, secrets


def is_production_mode() -> bool:
    """Returns True if running in production environment."""
    env = os.getenv("ENVIRONMENT", os.getenv("ENV", "development")).lower()
    return env in ("production", "prod") or bool(os.getenv("K_SERVICE"))


def get_model_names_for_environment() -> tuple[str, str]:
    """
    Returns (fast_model_name, reasoning_model_name) adhering to Enterprise SLA vs Preview policies:
    - In production (or when USE_GA_MODELS=true): returns GA models ('gemini-2.5-flash', 'gemini-2.5-pro')
      which carry Google Cloud Vertex AI 99.9% uptime enterprise SLA commitments.
    - In development/staging (or when USE_GA_MODELS=false): returns ('gemini-3-flash-preview', 'gemini-3-pro-preview').
    - Can be explicitly overridden via FAST_MODEL_NAME and REASONING_MODEL_NAME.
    """
    use_ga_env = os.getenv("USE_GA_MODELS")
    if use_ga_env is not None:
        use_ga = use_ga_env.lower() in ("true", "1", "yes")
    else:
        use_ga = is_production_mode()

    fast_default = "gemini-2.5-flash" if use_ga else "gemini-3-flash-preview"
    reasoning_default = "gemini-2.5-pro" if use_ga else "gemini-3-pro-preview"

    fast_model = os.getenv("FAST_MODEL_NAME", fast_default)
    reasoning_model = os.getenv("REASONING_MODEL_NAME", reasoning_default)
    return fast_model, reasoning_model


