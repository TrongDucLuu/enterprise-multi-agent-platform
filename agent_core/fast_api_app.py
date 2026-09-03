import os
import time
import logging
from fastapi import FastAPI, Depends, Query
from google.adk.cli.fast_api import get_fast_api_app
from google.cloud import logging as cloud_logging
from vertexai import agent_engines
from agent_core.app_utils.env import init_environment, is_production_mode
from agent_core.app_utils.sso_auth import (
    SSOUser,
    get_current_user,
    require_admin,
    create_dev_mock_token,
    SSOAuthenticationMiddleware,
    is_allow_local_dev_sso,
    check_cloud_identity_startup_access,
)
from agent_core.app_utils.semantic_cache import get_semantic_cache
from agent_core.app_utils.rate_limiter import RateLimitMiddleware

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
    name = os.environ.get("AGENT_ENGINE_MEMORY_BANK_NAME", "agent_core")
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
if is_production_mode():
    _blocked = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
    app.router.routes = [
        r for r in app.router.routes
        if getattr(r, "path", None) not in _blocked
    ]
    app.docs_url = None
    app.redoc_url = None
    app.openapi_url = None


# Middleware Stack (Starlette executes in reverse registration order - LIFO):
# 1. Inner layer: SSO Authentication Middleware (verifies JWT/OIDC after passing rate limiter)
app.add_middleware(SSOAuthenticationMiddleware)

# 2. Outer layer: Rate Limiting Middleware (executes FIRST on incoming requests to drop DDoS/bot spam)
app.add_middleware(RateLimitMiddleware)

from agent_core import CORE_VERSION
from agent_core.agent_builder import load_domain_pack

try:
    _pack_meta = load_domain_pack().get("pack_meta", {})
    PACK_ID = str(_pack_meta.get("id", "it-helpdesk"))
    PACK_VERSION = str(_pack_meta.get("version", "1.0.0"))
except Exception:
    PACK_ID = os.getenv("DOMAIN_PACK", "it-helpdesk")
    PACK_VERSION = "1.0.0"


import contextlib
from typing import AsyncGenerator

# ADK provides its own lifespan context manager inside get_fast_api_app which Starlette uses exclusively.
# We wrap ADK's lifespan to guarantee our startup self-checks run reliably on server startup.
_adk_lifespan = app.router.lifespan_context

@contextlib.asynccontextmanager
async def _wrapped_lifespan(app_instance: FastAPI) -> AsyncGenerator[None, None]:
    # 1. Startup phase: run self-checks
    try:
        check_cloud_identity_startup_access()
    except Exception as e:
        logger.warning(f"Startup self-check encountered unexpected error: {e}")

    # 2. Yield control to ADK's internal lifespan
    if _adk_lifespan:
        async with _adk_lifespan(app_instance) as maybe_state:
            yield maybe_state
    else:
        yield

app.router.lifespan_context = _wrapped_lifespan


# 1. System Health & Readiness Endpoints (Used by Cloud Run startup/liveness probes & Load Balancer)
@app.get("/healthz", tags=["Health"])
@app.get("/health", tags=["Health"])
@app.get("/livez", tags=["Health"])
async def health_check():
    """Liveness probe endpoint confirming container availability and pack metadata."""
    return {
        "status": "healthy",
        "service": "it-helpdesk-agent",
        "core_version": CORE_VERSION,
        "pack_id": PACK_ID,
        "pack_version": PACK_VERSION,
        "timestamp": time.time(),
    }


@app.get("/readyz", tags=["Health"])
async def readiness_check():
    """Readiness probe endpoint confirming system readiness."""
    from agent_core.app_utils.sso_auth import validate_sso_configuration
    is_valid, error_msg = validate_sso_configuration()
    if not is_valid:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Service not ready: {error_msg}",
        )
    return {
        "status": "ready",
        "service": "it-helpdesk-agent",
        "core_version": CORE_VERSION,
        "pack_id": PACK_ID,
        "pack_version": PACK_VERSION,
    }


# 2. Authenticated user profile inspection endpoint
@app.get("/api/auth/me", tags=["Authentication"])
async def get_sso_user_profile(user: SSOUser = Depends(get_current_user)):
    """Returns the authenticated SSO employee profile."""
    return {
        "status": "authenticated",
        "user": user.model_dump()
    }

# 3. Semantic Cache Inspection & Fast Lookup Endpoints (Requires Admin)
@app.get("/api/cache/stats", tags=["Optimization"])
async def get_semantic_cache_stats(user: SSOUser = Depends(require_admin)):
    """Returns real-time statistics of the semantic cache."""
    cache = get_semantic_cache()
    return {
        "status": "success",
        "stats": cache.get_stats()
    }

@app.get("/api/cache/query", tags=["Optimization"])
async def query_semantic_cache(
    q: str = Query(..., description="User question to check in cache"),
    threshold: float = Query(0.92, description="Similarity threshold (0.85 to 1.0)"),
    user: SSOUser = Depends(require_admin)
):
    """Performs instant sub-50ms semantic cache lookup with user isolation."""
    if threshold < 0.85:
        from fastapi import HTTPException, status
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Similarity threshold must be >= 0.85 to prevent unauthorized content enumeration."
        )
    cache = get_semantic_cache()
    match = cache.get(q, user_id=user.user_id, similarity_threshold=threshold)
    if match:
        return {"status": "hit", "result": match}
    return {"status": "miss", "message": "No semantically similar query found in cache."}

# 4. Product Analytics & Telemetry Aggregation Endpoint (Requires Admin)
@app.get("/api/analytics/instance-summary", tags=["Product Telemetry & Analytics"])
@app.get("/api/analytics/summary", tags=["Product Telemetry & Analytics"])
async def get_analytics_summary(user: SSOUser = Depends(require_admin)):
    """Returns aggregated single-instance product metrics: Cache Hit Rate, Tier Distribution, and Query Latency."""
    from agent_core.app_utils.telemetry import ProductMetricsCollector
    stats = ProductMetricsCollector.get_summary_stats()
    stats["scope"] = "single_instance"
    return stats

# 5. Development-Only Mock Token Minting Route (Omitted in Production)
if is_allow_local_dev_sso():
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


def is_enable_a2a_endpoint() -> bool:
    """Returns True if the Agent-to-Agent (A2A) protocol endpoint is enabled."""
    return os.getenv("ENABLE_A2A_ENDPOINT", "false").lower() in ("true", "1", "yes")


def setup_a2a_endpoint(app_instance: FastAPI):
    """
    Initializes and mounts the A2A (Agent-to-Agent) protocol endpoint at /a2a.
    Dynamically generates the AgentCard from the active domain pack and ADK agent hierarchy.
    Enforces parent middleware authentication (SSOAuthenticationMiddleware, RateLimitMiddleware).
    """
    if not is_enable_a2a_endpoint():
        return None

    try:
        import asyncio
        import concurrent.futures
        from google.adk.a2a.utils.agent_to_a2a import to_a2a
        from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder
        from agent_core.agent_builder import build_agent_system, load_domain_pack

        root_agent, created_agents = build_agent_system()
        pack_info = load_domain_pack()
        pack_meta = pack_info.get("pack_meta", {})

        async def _init_card():
            card = await AgentCardBuilder(
                agent=root_agent,
                rpc_url="/a2a/",
            ).build()
            if pack_meta.get("name"):
                card.name = str(pack_meta.get("name")).lower().replace(" ", "_").replace("-", "_")
            if pack_meta.get("description"):
                card.description = str(pack_meta.get("description"))
            return card

        try:
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                agent_card = pool.submit(lambda: asyncio.run(_init_card())).result()
        except RuntimeError:
            agent_card = asyncio.run(_init_card())

        a2a_sub_app = to_a2a(
            agent=root_agent,
            agent_card=agent_card,
        )

        async def _init_routes():
            async with a2a_sub_app.router.lifespan_context(a2a_sub_app):
                pass

        try:
            loop = asyncio.get_running_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pool.submit(lambda: asyncio.run(_init_routes())).result()
        except RuntimeError:
            asyncio.run(_init_routes())

        app_instance.mount("/a2a", a2a_sub_app)
        logger.info(
            "A2A Protocol endpoint successfully initialized and mounted at /a2a for domain pack '%s'",
            pack_meta.get("id", "unknown")
        )
        return a2a_sub_app
    except Exception as e:
        logger.error("Failed to initialize A2A endpoint: %s", e, exc_info=True)
        return None


# Initialize A2A endpoint if enabled via feature flag
setup_a2a_endpoint(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
