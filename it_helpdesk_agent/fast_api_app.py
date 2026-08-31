import os
import time
import logging
from fastapi import FastAPI, Depends, Query
from google.adk.cli.fast_api import get_fast_api_app
from google.cloud import logging as cloud_logging
from vertexai import agent_engines
from it_helpdesk_agent.app_utils.env import init_environment
from it_helpdesk_agent.app_utils.sso_auth import (
    SSOUser,
    get_current_user,
    create_dev_mock_token,
    SSOAuthenticationMiddleware,
    ALLOW_LOCAL_DEV_SSO,
)
from it_helpdesk_agent.app_utils.semantic_cache import get_semantic_cache
from it_helpdesk_agent.app_utils.rate_limiter import RateLimitMiddleware

PROJECT_ID, MODEL_LOC, SERVICE_LOC, SECRETS = init_environment()

try:
    logger = cloud_logging.Client().logger(__name__)
except Exception:
    logger = logging.getLogger(__name__)

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUCKET = os.environ.get("AI_ASSETS_BUCKET")
USE_IN_MEMORY = os.environ.get("USE_IN_MEMORY_SESSION", "").lower() in ("true", "1")
OTEL_TO_CLOUD = os.environ.get("OTEL_TO_CLOUD", "false").lower() in ("true", "1")


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
    otel_to_cloud=OTEL_TO_CLOUD,
)

# Enterprise Security Hardening: Disable Swagger UI, ReDoc, and OpenAPI schema in Production
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production" or bool(os.getenv("K_SERVICE"))
if IS_PRODUCTION:
    app.docs_url = None
    app.redoc_url = None
    app.openapi_url = None


# Middleware Stack (Starlette executes in reverse registration order - LIFO):
# 1. Inner layer: SSO Authentication Middleware (verifies JWT/OIDC after passing rate limiter)
app.add_middleware(SSOAuthenticationMiddleware)

# 2. Outer layer: Rate Limiting Middleware (executes FIRST on incoming requests to drop DDoS/bot spam)
app.add_middleware(RateLimitMiddleware)

# 1. System Health & Readiness Endpoints (Used by Cloud Run startup/liveness probes & Load Balancer)
@app.get("/healthz", tags=["Health"])
@app.get("/health", tags=["Health"])
async def health_check():
    """Liveness probe endpoint confirming container availability."""
    return {
        "status": "healthy",
        "service": "it-helpdesk-agent",
        "timestamp": time.time()
    }


@app.get("/readyz", tags=["Health"])
async def readiness_check():
    """Readiness probe endpoint confirming system readiness."""
    return {
        "status": "ready",
        "service": "it-helpdesk-agent"
    }


# 2. Authenticated user profile inspection endpoint
@app.get("/api/auth/me", tags=["Authentication"])
async def get_sso_user_profile(user: SSOUser = Depends(get_current_user)):
    """Returns the authenticated SSO employee profile."""
    return {
        "status": "authenticated",
        "user": user.model_dump()
    }

# 3. Semantic Cache Inspection & Fast Lookup Endpoints
@app.get("/api/cache/stats", tags=["Optimization"])
async def get_semantic_cache_stats(user: SSOUser = Depends(get_current_user)):
    """Returns real-time statistics of the semantic cache."""
    cache = get_semantic_cache()
    return {
        "status": "success",
        "stats": cache.get_stats()
    }

@app.get("/api/cache/query", tags=["Optimization"])
async def query_semantic_cache(
    q: str = Query(..., description="User question to check in cache"),
    threshold: float = Query(0.92, description="Similarity threshold (0.0 to 1.0)"),
    user: SSOUser = Depends(get_current_user)
):
    """Performs instant sub-50ms semantic cache lookup with user isolation."""
    cache = get_semantic_cache()
    match = cache.get(q, user_id=user.user_id, similarity_threshold=threshold)
    if match:
        return {"status": "hit", "result": match}
    return {"status": "miss", "message": "No semantically similar query found in cache."}

# 4. Product Analytics & Telemetry Aggregation Endpoint
@app.get("/api/analytics/summary", tags=["Product Telemetry & Analytics"])
async def get_analytics_summary(user: SSOUser = Depends(get_current_user)):
    """Returns aggregated product metrics: Cache Hit Rate, Tier Distribution, and Query Latency."""
    from it_helpdesk_agent.app_utils.telemetry import ProductMetricsCollector
    return ProductMetricsCollector.get_summary_stats()

# 5. Development-Only Mock Token Minting Route (Omitted in Production)
if ALLOW_LOCAL_DEV_SSO:
    @app.post("/api/auth/dev-token", tags=["Development Only"])
    async def generate_dev_sso_token(
        email: str = "employee@company.com",
        name: str = "Enterprise Employee",
        department: str = "IT",
        roles: str = "employee,it_admin"
    ):
        """
        Generates a signed mock SSO JWT token for local testing.
        Strictly prohibited and inaccessible in production mode.
        """
        logger.warning(f"DEV SECURITY WARNING: Generating mock dev token for {email}")
        user = SSOUser(
            user_id=email.split("@")[0],
            email=email,
            email_verified=True,
            full_name=name,
            department=department,
            roles=[r.strip() for r in roles.split(",") if r.strip()],
            is_authenticated=True
        )
        token = create_dev_mock_token(user)
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "user": user.model_dump()
        }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
