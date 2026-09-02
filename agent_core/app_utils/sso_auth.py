import contextvars
import datetime
import logging
import os
import threading
import time
from typing import Optional
import jwt
import requests
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from agent_core.app_utils.env import is_production_mode

logger = logging.getLogger(__name__)
security_scheme = HTTPBearer(auto_error=False)

ALLOW_LOCAL_DEV_SSO: Optional[bool] = None

def is_allow_local_dev_sso() -> bool:
    """
    Evaluates whether local dev mock SSO tokens are allowed.
    Strict Fail-Closed Default: In production or by default, local dev SSO is strictly False.
    Must be explicitly enabled via ALLOW_LOCAL_DEV_SSO=true in non-production environments.
    """
    if is_production_mode():
        return False
    global ALLOW_LOCAL_DEV_SSO
    if ALLOW_LOCAL_DEV_SSO is not None:
        return bool(ALLOW_LOCAL_DEV_SSO)
    return os.getenv("ALLOW_LOCAL_DEV_SSO", "false").lower() in ("true", "1", "yes")

# Dynamic property aliases for backward compatibility
IS_PRODUCTION = is_production_mode()

SSO_CLIENT_ID = os.getenv("SSO_CLIENT_ID", "it-helpdesk-agent-client-id")
SSO_ISSUER = os.getenv("SSO_ISSUER", "https://accounts.google.com")
ALLOWED_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
if SSO_ISSUER:
    ALLOWED_ISSUERS.add(SSO_ISSUER)

def get_allowed_domains() -> list[str]:
    raw_allowed_domains = os.getenv("ALLOWED_DOMAINS", "").strip()
    return [d.strip().lower() for d in raw_allowed_domains.split(",") if d.strip()]

ALLOWED_DOMAINS = get_allowed_domains()

DEV_JWT_SECRET = os.getenv("SSO_JWT_SECRET", "dev-only-secret-key-change-in-production-2026-xyz")

# Singleton Request Adapter with Session Connection Pool for fast JWKS lookups
_SHARED_SESSION = requests.Session()
_SHARED_GOOGLE_REQUEST_ADAPTER = google_requests.Request(session=_SHARED_SESSION)

# Cloud Identity Singleton Client & Cache
_CLOUD_IDENTITY_SERVICE = None
_CLOUD_IDENTITY_LOCK = threading.Lock()

def _get_cloud_identity_service():
    global _CLOUD_IDENTITY_SERVICE
    if _CLOUD_IDENTITY_SERVICE is None:
        with _CLOUD_IDENTITY_LOCK:
            if _CLOUD_IDENTITY_SERVICE is None:
                from googleapiclient import discovery
                import google.auth
                credentials, project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-identity.groups.readonly"])
                _CLOUD_IDENTITY_SERVICE = discovery.build("cloudidentity", "v1", credentials=credentials, cache_discovery=False)
    return _CLOUD_IDENTITY_SERVICE

_WORKSPACE_GROUPS_CACHE: dict[str, tuple[float, list[str]]] = {}
_WORKSPACE_GROUPS_CACHE_TTL = 300.0  # 5 minutes for valid group memberships
_WORKSPACE_GROUPS_ERROR_CACHE_TTL = 60.0  # 1 minute negative caching for errors/empty lookups
_WORKSPACE_GROUPS_CACHE_MAX_SIZE = 10000  # Bounded cache size to prevent unbounded memory growth


def _store_workspace_groups_cache(email: str, timestamp: float, groups: list[str]) -> None:
    """Stores groups in cache with bounded size enforcement."""
    if len(_WORKSPACE_GROUPS_CACHE) >= _WORKSPACE_GROUPS_CACHE_MAX_SIZE and email not in _WORKSPACE_GROUPS_CACHE:
        # Evict oldest entry (FIFO)
        try:
            oldest_key = next(iter(_WORKSPACE_GROUPS_CACHE))
            _WORKSPACE_GROUPS_CACHE.pop(oldest_key, None)
        except (StopIteration, KeyError):
            pass
    _WORKSPACE_GROUPS_CACHE[email] = (timestamp, groups)


def check_cloud_identity_startup_access(timeout: float = 5.0) -> bool:
    """
    Performs a single startup self-check if ENABLE_CLOUD_IDENTITY_GROUP_LOOKUP is enabled.
    Logs a high-visibility ERROR once if Cloud Identity API returns 403 Forbidden, alerting DevOps
    that the Service Account requires Google Workspace Admin Console role assignment ('Groups Reader')
    or Domain-Wide Delegation.
    """
    import concurrent.futures

    enabled = os.getenv("ENABLE_CLOUD_IDENTITY_GROUP_LOOKUP", os.getenv("GOOGLE_WORKSPACE_GROUPS_ENABLED", "false")).lower() in ("true", "1", "yes")
    if not enabled:
        return True

    try:
        service = _get_cloud_identity_service()
        # Probe using dummy healthcheck member_key_id
        request = service.groups().memberships().searchTransitiveGroups(
            parent="groups/-",
            query="member_key_id == 'healthcheck-probe@domain.invalid'",
        )
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(request.execute)
            future.result(timeout=timeout)
        logger.info("Cloud Identity API startup probe succeeded.")
        return True
    except concurrent.futures.TimeoutError:
        logger.warning("Cloud Identity startup probe timed out after %.1fs; continuing startup.", timeout)
        return False
    except Exception as exc:
        err_str = str(exc)
        if "403" in err_str or "PermissionDenied" in err_str or "forbidden" in err_str.lower():
            logger.error(
                "Cloud Identity Groups API returned 403 Forbidden during startup self-check. "
                "The GCP Service Account lacks permissions to read Google Workspace groups. "
                "Action Required: Grant the Service Account the 'Groups Reader' admin role in Google Workspace Admin Console "
                "(admin.google.com -> Account -> Admin roles) or configure Domain-Wide Delegation. "
                "Group lookup will fail closed and fallback to token claims. Error: %s",
                exc,
            )
        else:
            logger.warning("Cloud Identity startup probe returned non-fatal error: %s", exc)
        return False


def fetch_google_workspace_groups(user_email: str) -> list[str]:
    """
    Fetches transitive group memberships for a Google Workspace user via Cloud Identity API.
    Enabled when ENABLE_CLOUD_IDENTITY_GROUP_LOOKUP=true or GOOGLE_WORKSPACE_GROUPS_ENABLED=true.
    Uses singleton discovery client and caches both hits (5m) and errors (1m) to avoid latency penalties.
    Gracefully falls back to empty list on network or permission errors.
    """
    if not user_email or "@" not in user_email:
        return []

    enabled = os.getenv("ENABLE_CLOUD_IDENTITY_GROUP_LOOKUP", os.getenv("GOOGLE_WORKSPACE_GROUPS_ENABLED", "false")).lower() in ("true", "1", "yes")
    if not enabled:
        return []

    now = time.time()
    if user_email in _WORKSPACE_GROUPS_CACHE:
        cached_time, cached_groups = _WORKSPACE_GROUPS_CACHE[user_email]
        ttl = _WORKSPACE_GROUPS_CACHE_TTL if cached_groups else _WORKSPACE_GROUPS_ERROR_CACHE_TTL
        if now - cached_time < ttl:
            return cached_groups

    try:
        service = _get_cloud_identity_service()
        clean_email = user_email.replace("\\", "\\\\").replace("'", "\\'")
        request = service.groups().memberships().searchTransitiveGroups(
            parent="groups/-",
            query=f"member_key_id == '{clean_email}'",
        )
        response = request.execute()
        memberships = response.get("memberships", [])
        groups = []
        for m in memberships:
            group_key = m.get("groupKey", {})
            group_id = group_key.get("id")
            if group_id:
                groups.append(group_id.lower())
        _store_workspace_groups_cache(user_email, now, groups)
        return groups
    except Exception as exc:
        logger.warning("Cloud Identity / Google Workspace group lookup failed for %s: %s", user_email, exc)
        _store_workspace_groups_cache(user_email, now, [])
        return []


class SSOUser(BaseModel):
    user_id: str = Field(description="Unique User ID / Sub / Email")
    email: str = Field(description="Company Email")
    email_verified: bool = Field(default=True, description="Email verification status")
    full_name: str = Field(default="Employee", description="Full Name")
    department: str = Field(default="General", description="Company Department")
    roles: list[str] = Field(default_factory=lambda: ["employee"], description="Assigned Roles")
    groups: list[str] = Field(default_factory=list, description="Enterprise Directory Groups")
    hosted_domain: Optional[str] = Field(default=None, description="Google Workspace Hosted Domain (hd)")
    is_authenticated: bool = True


# ContextVar to maintain current authenticated user context across async tool calls
current_sso_user: contextvars.ContextVar[Optional[SSOUser]] = contextvars.ContextVar(
    "current_sso_user", default=None
)
current_sso_raw_token: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_sso_raw_token", default=None
)


def get_current_sso_user() -> Optional[SSOUser]:
    """Retrieves the authenticated SSO user from the current async context."""
    return current_sso_user.get()


def get_current_sso_token() -> Optional[str]:
    """Retrieves the raw OIDC/JWT token from the current context or generates dev token if permitted."""
    token = current_sso_raw_token.get()
    if token is None and is_allow_local_dev_sso():
        user = get_current_sso_user()
        if user:
            try:
                return create_dev_mock_token(user)
            except Exception:
                return None
    return token


def require_role(required_roles: list[str]) -> tuple[bool, Optional[str]]:
    """
    Checks if the active authenticated SSO user possesses at least one of the required roles.
    Returns (is_allowed, error_message).
    """
    user = get_current_sso_user()
    if not user:
        return False, "Yêu cầu đăng nhập xác thực SSO trước khi sử dụng công cụ này."

    user_roles = [r.lower() for r in user.roles]
    needed = [r.lower() for r in required_roles]
    if any(r in user_roles for r in needed):
        return True, None

    return (
        False,
        f"Truy cập bị từ chối: Quyền hạn hiện tại ({user.roles}) không đủ. "
        f"Công cụ này yêu cầu một trong các vai trò: {required_roles}.",
    )


def create_dev_mock_token(
    user: SSOUser,
    secret_key: Optional[str] = None,
    expires_in_seconds: int = 3600,
    issuer: Optional[str] = None,
    audience: Optional[str] = None,
) -> str:
    """
    Creates a signed HMAC-SHA256 mock JWT token for local testing.
    Strictly prohibited in production mode.
    """
    if not is_allow_local_dev_sso():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev token creation is strictly disabled in production.",
        )

    key = secret_key or DEV_JWT_SECRET
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": user.user_id,
        "email": user.email,
        "email_verified": user.email_verified,
        "name": user.full_name,
        "department": user.department,
        "roles": user.roles,
        "groups": getattr(user, "groups", []),
        "hd": user.hosted_domain,
        "iss": issuer or SSO_ISSUER,
        "aud": audience or SSO_CLIENT_ID,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    return jwt.encode(payload, key, algorithm="HS256")


# Backward compatibility alias for tests
create_sso_token = create_dev_mock_token


def verify_google_oidc_token(
    token: str,
    client_id: Optional[str] = None,
    allowed_domains: Optional[list[str]] = None,
    request_adapter: Optional[google_requests.Request] = None,
) -> SSOUser:
    """
    Validates a Google ID Token (OIDC) against Google's public JWKS certificates.
    Strictly checks RS256 signature, expiry, audience (Client ID), and hosted domain (hd).
    Fail-closed: In production, ALLOWED_DOMAINS is strictly required.
    """
    expected_client_id = client_id or os.getenv("SSO_CLIENT_ID", "it-helpdesk-agent-client-id")
    domains_to_check = allowed_domains if allowed_domains is not None else get_allowed_domains()
    is_prod = is_production_mode()

    # P1.1: Fail-closed verification in production if ALLOWED_DOMAINS is missing
    if is_prod and not domains_to_check:
        logger.error("Enterprise Security Violation: ALLOWED_DOMAINS is not configured in production.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cấu hình bảo mật lỗi: ALLOWED_DOMAINS bắt buộc phải được thiết lập trong môi trường production.",
        )

    try:
        req = request_adapter or _SHARED_GOOGLE_REQUEST_ADAPTER
        payload = id_token.verify_oauth2_token(
            token,
            req,
            audience=expected_client_id if expected_client_id else None,
        )

        iss = payload.get("iss", "")
        if iss not in ALLOWED_ISSUERS:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid OIDC token issuer '{iss}'. Expected one of {list(ALLOWED_ISSUERS)}.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if not payload.get("email_verified", False):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email address in OIDC token is not verified.",
                headers={"WWW-Authenticate": "Bearer"},
            )

        email = payload.get("email", "")
        hd = payload.get("hd")

        # Enterprise Hosted Domain Authorization Check
        if domains_to_check:
            domain_matched = False
            if hd and hd.lower() in [d.lower() for d in domains_to_check]:
                domain_matched = True
            elif any(email.lower().endswith(f"@{d.lower()}") for d in domains_to_check):
                domain_matched = True

            if not domain_matched:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied: Account domain not permitted. Allowed domains: {domains_to_check}.",
                )

        from agent_core.app_utils.system_config import resolve_user_roles

        groups_claim_name = os.getenv("SSO_GROUPS_CLAIM", "groups")
        raw_groups = payload.get(groups_claim_name) or payload.get("groups", [])
        if isinstance(raw_groups, str):
            raw_groups = [g.strip() for g in raw_groups.split(",") if g.strip()]
        elif not isinstance(raw_groups, list):
            raw_groups = [str(raw_groups)] if raw_groups else []

        if not raw_groups:
            ws_groups = fetch_google_workspace_groups(email)
            if ws_groups:
                raw_groups = ws_groups

        assigned_roles = resolve_user_roles(email, payload.get("roles"), raw_groups)

        return SSOUser(
            user_id=payload.get("sub", email),
            email=email,
            email_verified=True,
            full_name=payload.get("name", "Enterprise Employee"),
            department=payload.get("department", "General"),
            roles=assigned_roles,
            groups=raw_groups,
            hosted_domain=hd,
            is_authenticated=True,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Google OIDC verification failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Google OIDC Token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_dev_mock_token(
    token: str,
    secret_key: Optional[str] = None,
    allowed_issuer: Optional[str] = None,
    allowed_audience: Optional[str] = None,
) -> SSOUser:
    """
    Validates a local dev/test mock JWT token using HS256 algorithm ONLY.
    Strictly isolated from RS256 to eliminate Algorithm Confusion vulnerabilities.
    """
    if not is_allow_local_dev_sso():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dev Mock HMAC authentication is strictly disabled in production mode.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key = secret_key or DEV_JWT_SECRET
    if not key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SSO_JWT_SECRET is not configured for dev mock token verification.",
        )

    expected_iss = allowed_issuer or SSO_ISSUER
    expected_aud = allowed_audience or SSO_CLIENT_ID

    try:
        # Strictly enforce HS256 ONLY
        payload = jwt.decode(
            token,
            key,
            algorithms=["HS256"],
            issuer=expected_iss if expected_iss else None,
            audience=expected_aud if expected_aud else None,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": bool(expected_iss),
                "verify_aud": bool(expected_aud),
            },
        )
        from agent_core.app_utils.system_config import resolve_user_roles

        groups_claim_name = os.getenv("SSO_GROUPS_CLAIM", "groups")
        raw_groups = payload.get(groups_claim_name) or payload.get("groups", [])
        if isinstance(raw_groups, str):
            raw_groups = [g.strip() for g in raw_groups.split(",") if g.strip()]
        elif not isinstance(raw_groups, list):
            raw_groups = [str(raw_groups)] if raw_groups else []

        email = payload.get("email", "dev@company.com")
        raw_roles = payload.get("roles", ["employee"])
        assigned_roles = resolve_user_roles(email, raw_roles, raw_groups)

        return SSOUser(
            user_id=payload.get("sub", payload.get("email", "dev_user")),
            email=email,
            email_verified=True,
            full_name=payload.get("name", "Local Dev Employee"),
            department=payload.get("department", "Engineering"),
            roles=assigned_roles,
            groups=raw_groups,
            hosted_domain=payload.get("hd"),
            is_authenticated=True,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="SSO Token has expired. Please re-authenticate.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid Dev Mock Token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


def verify_sso_token(
    token: str,
    secret_key: Optional[str] = None,
    allowed_issuer: Optional[str] = None,
    allowed_audience: Optional[str] = None,
) -> SSOUser:
    """
    Unified SSO Token Verifier with algorithm isolation:
    - RS256: Verified against Google OIDC JWKS.
    - HS256: Verified via Dev Mock Token ONLY when ALLOW_LOCAL_DEV_SSO=true.
    - Other algorithms: Rejected immediately.
    """
    try:
        header = jwt.get_unverified_header(token)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Malformed JWT Header: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

    alg = header.get("alg")
    if alg == "RS256":
        return verify_google_oidc_token(token, client_id=allowed_audience)
    elif alg == "HS256":
        if not is_allow_local_dev_sso():
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="HMAC (HS256) tokens are prohibited in production mode. Use Google OIDC (RS256).",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return verify_dev_mock_token(
            token,
            secret_key=secret_key,
            allowed_issuer=allowed_issuer,
            allowed_audience=allowed_audience,
        )
    else:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Unsupported JWT algorithm '{alg}'. Only RS256 (OIDC) or HS256 (Dev) are permitted.",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme),
) -> SSOUser:
    """
    FastAPI dependency that extracts and validates the Bearer SSO token from Authorization header.
    """
    if not credentials:
        if is_allow_local_dev_sso():
            logger.info("Using default local development SSO user context.")
            return SSOUser(
                user_id="dev-user-001",
                email="dev.employee@company.com",
                email_verified=True,
                full_name="Local Dev Employee",
                department="Engineering",
                roles=["employee", "it_admin"],
                is_authenticated=True,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization Bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    return verify_sso_token(token)


class SSOAuthenticationMiddleware(BaseHTTPMiddleware):
    """
    Global Authentication Middleware protecting ALL endpoints (ADK Agent endpoints, APIs, Sessions)
    except explicit public whitelisted paths.
    Propagates authenticated SSO user into ContextVar for RBAC checks in tools.
    """
    PUBLIC_PATHS = {
        "/healthz",
        "/health",
        "/readyz",
        "/livez",
        "/docs",
        "/openapi.json",
        "/redoc",
        "/favicon.ico",
    }
    PUBLIC_PREFIXES = (
        "/static/",
        "/assets/",
    )

    @property
    def public_paths(self) -> set[str]:
        if is_production_mode():
            return {
                "/healthz",
                "/health",
                "/readyz",
                "/livez",
                "/favicon.ico",
            }
        return self.PUBLIC_PATHS

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. Allow public whitelisted paths
        if path in self.public_paths or any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)

        # 2. Extract Authorization Bearer token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            if is_allow_local_dev_sso():
                dev_user = SSOUser(
                    user_id="dev-user-001",
                    email="dev.employee@company.com",
                    email_verified=True,
                    full_name="Local Dev Employee",
                    department="Engineering",
                    roles=["employee", "it_admin"],
                    is_authenticated=True,
                )
                request.state.user = dev_user
                token_ctx = current_sso_user.set(dev_user)
                mock_raw = create_dev_mock_token(dev_user)
                raw_token_ctx = current_sso_raw_token.set(mock_raw)
                try:
                    return await call_next(request)
                finally:
                    current_sso_user.reset(token_ctx)
                    current_sso_raw_token.reset(raw_token_ctx)

            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required. Missing Authorization Bearer header."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:].strip()
        try:
            cached_user = getattr(request.state, "verified_sso_user", None)
            cached_err = getattr(request.state, "sso_auth_error", None)
            if cached_user is not None:
                user = cached_user
            elif cached_err is not None:
                raise cached_err
            else:
                user = verify_sso_token(token)

            request.state.user = user
            token_ctx = current_sso_user.set(user)
            raw_token_ctx = current_sso_raw_token.set(token)
            try:
                return await call_next(request)
            finally:
                current_sso_user.reset(token_ctx)
                current_sso_raw_token.reset(raw_token_ctx)
        except HTTPException as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers or {"WWW-Authenticate": "Bearer"},
            )
        except Exception as exc:
            return JSONResponse(
                status_code=401,
                content={"detail": f"Authentication failed: {exc}"},
                headers={"WWW-Authenticate": "Bearer"},
            )


def validate_sso_configuration() -> tuple[bool, Optional[str]]:
    """
    Validates SSO configuration for readiness probes and startup integrity.
    Fails closed in production if ALLOWED_DOMAINS is not configured.
    """
    if is_production_mode():
        raw_domains = os.getenv("ALLOWED_DOMAINS", "").strip()
        domains = [d.strip().lower() for d in raw_domains.split(",") if d.strip()]
        if not domains:
            return False, "Cấu hình bảo mật lỗi: ALLOWED_DOMAINS bắt buộc phải được thiết lập trong môi trường production."
        allow_dev = os.getenv("ALLOW_LOCAL_DEV_SSO", "false").lower() in ("true", "1", "yes")
        if allow_dev:
            return False, "Cấu hình bảo mật lỗi: ALLOW_LOCAL_DEV_SSO phải bị vô hiệu hóa trong môi trường production."
    return True, None
