import os
import logging
from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app
from google.cloud import logging as cloud_logging
from vertexai import agent_engines
from it_helpdesk_agent.app_utils.env import init_environment

PROJECT_ID, MODEL_LOC, SERVICE_LOC, SECRETS = init_environment()

try:
    logger = cloud_logging.Client().logger(__name__)
except Exception:
    logger = logging.getLogger(__name__)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUCKET = os.environ.get("AI_ASSETS_BUCKET")
USE_IN_MEMORY = os.environ.get("USE_IN_MEMORY_SESSION", "").lower() in ("true", "1")

def _get_memory_bank_uri() -> tuple[str | None, str | None]:
    """Resolves the Memory Bank / Agent Engine URI with graceful fallbacks."""
    if USE_IN_MEMORY:
        return None, None

    # Priority 1: Direct Resource Name provided by environment/Terraform
    direct_resource = os.environ.get("AGENT_ENGINE_RESOURCE_NAME")
    if direct_resource:
        uri = f"agentengine://{direct_resource}"
        print(f"Connecting to IT Helpdesk Memory Bank: {uri}")
        return uri, uri

    # Priority 2: Lookup by display name or create lazily if permitted
    name = os.environ.get("AGENT_ENGINE_MEMORY_BANK_NAME", "it_helpdesk_agent")
    try:
        existing = list(agent_engines.list(filter=f"display_name={name}"))
        ae = existing[0] if existing else agent_engines.create(display_name=name)
        uri = f"agentengine://{ae.resource_name}"
        print(f"Connecting to Memory Bank: {uri} (display_name={name})")
        return uri, uri
    except Exception as e:
        print(f"Warning: Could not connect to Vertex AI Agent Engine '{name}': {e}. Falling back to in-memory.")
        return None, None

from fastapi import Depends
from it_helpdesk_agent.app_utils.sso_auth import SSOUser, get_current_user, create_sso_token

SESSION_URI, MEMORY_URI = _get_memory_bank_uri()

raw_origins = os.getenv("ALLOW_ORIGINS", "")
allow_origins = [origin.strip() for origin in raw_origins.split(",") if origin.strip()] if raw_origins else None

app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    artifact_service_uri=f"gs://{BUCKET}" if BUCKET else None,
    allow_origins=allow_origins,
    session_service_uri=SESSION_URI,
    memory_service_uri=MEMORY_URI,
    otel_to_cloud=True,
)

# --- SSO Authentication Endpoints ---
@app.get("/api/auth/me", tags=["Authentication"])
async def get_sso_user_profile(user: SSOUser = Depends(get_current_user)):
    """Returns the authenticated SSO employee profile."""
    return {
        "status": "authenticated",
        "user": user.model_dump()
    }

@app.post("/api/auth/dev-token", tags=["Authentication"])
async def generate_dev_sso_token(
    email: str = "employee@company.com",
    name: str = "Enterprise Employee",
    department: str = "IT",
    roles: str = "employee,it_admin"
):
    """Generates a signed mock SSO JWT token for local testing and API integration."""
    user = SSOUser(
        user_id=email.split("@")[0],
        email=email,
        full_name=name,
        department=department,
        roles=[r.strip() for r in roles.split(",") if r.strip()],
        is_authenticated=True
    )
    token = create_sso_token(user)
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "user": user.model_dump()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)

