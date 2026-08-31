import contextvars
import datetime
import logging
import os
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

logger = logging.getLogger(__name__)
security_scheme = HTTPBearer(auto_error=False)

# Security Environment & Flags
ENVIRONMENT = os.getenv("ENVIRONMENT", "development").lower()
IS_PRODUCTION = ENVIRONMENT == "production" or bool(os.getenv("K_SERVICE"))

# Strict Fail-Closed Default:
# In production, ALLOW_LOCAL_DEV_SSO is ALWAYS False regardless of env variable.
ALLOW_LOCAL_DEV_SSO = (not IS_PRODUCTION) and (os.getenv("ALLOW_LOCAL_DEV_SSO", "false").lower() in ("true", "1"))

SSO_CLIENT_ID = os.getenv("SSO_CLIENT_ID", "it-helpdesk-agent-client-id")
SSO_ISSUER = os.getenv("SSO_ISSUER", "https://accounts.google.com")
ALLOWED_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
if SSO_ISSUER:
    ALLOWED_ISSUERS.add(SSO_ISSUER)

raw_allowed_domains = os.getenv("ALLOWED_DOMAINS", "")
ALLOWED_DOMAINS = [d.strip().lower() for d in raw_allowed_domains.split(",") if d.strip()]

DEV_JWT_SECRET = os.getenv("SSO_JWT_SECRET", "dev-only-secret-key-change-in-production-2026-xyz")

# Singleton Request Adapter with Session Connection Pool for fast JWKS lookups
_SHARED_SESSION = requests.Session()
_SHARED_GOOGLE_REQUEST_ADAPTER = google_requests.Request(session=_SHARED_SESSION)


class SSOUser(BaseModel):
    user_id: str = Field(description="Unique User ID / Sub / Email")
    email: str = Field(description="Company Email")
    email_verified: bool = Field(default=True, description="Email verification status")
    full_name: str = Field(default="Employee", description="Full Name")
    department: str = Field(default="General", description="Company Department")
    roles: list[str] = Field(default_factory=lambda: ["employee"], description="Assigned Roles")
    hosted_domain: Optional[str] = Field(default=None, description="Google Workspace Hosted Domain (hd)")
    is_authenticated: bool = True


# ContextVar to maintain current authenticated user context across async tool calls
current_sso_user: contextvars.ContextVar[Optional[SSOUser]] = contextvars.ContextVar(
    "current_sso_user", default=None
)


def get_current_sso_user() -> Optional[SSOUser]:
    """Retrieves the authenticated SSO user from the current async context."""
    user = current_sso_user.get()
    if user is None and ALLOW_LOCAL_DEV_SSO:
        # Provide default mock user context in local dev mode
        return SSOUser(
            user_id="dev-user-001",
            email="dev.employee@company.com",
            email_verified=True,
            full_name="Local Dev Employee",
            department="Engineering",
            roles=["employee", "it_admin"],
            is_authenticated=True,
        )
    return user


def require_role(required_roles: list[str]) -> tuple[bool, Optional[str]]:
    """
    Checks if the active authenticated SSO user possesses at least one of the required roles.
    Returns (is_allowed, error_message).
    """
    user = get_current_sso_user()
    if not user:
        if ALLOW_LOCAL_DEV_SSO:
            return True, None
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
    if not ALLOW_LOCAL_DEV_SSO:
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
    expected_client_id = client_id or SSO_CLIENT_ID
    domains_to_check = allowed_domains if allowed_domains is not None else ALLOWED_DOMAINS

    # P1.1: Fail-closed verification in production if ALLOWED_DOMAINS is missing
    if IS_PRODUCTION and not domains_to_check:
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

        return SSOUser(
            user_id=payload.get("sub", email),
            email=email,
            email_verified=True,
            full_name=payload.get("name", "Enterprise Employee"),
            department=payload.get("department", "General"),
            roles=payload.get("roles", ["employee"]),
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
    if not ALLOW_LOCAL_DEV_SSO:
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
        return SSOUser(
            user_id=payload.get("sub", payload.get("email", "dev_user")),
            email=payload.get("email", "dev@company.com"),
            email_verified=True,
            full_name=payload.get("name", "Local Dev Employee"),
            department=payload.get("department", "Engineering"),
            roles=payload.get("roles", ["employee"]),
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
        if not ALLOW_LOCAL_DEV_SSO:
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
        if ALLOW_LOCAL_DEV_SSO:
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
        "/docs",
        "/openapi.json",
        "/redoc",
        "/favicon.ico",
    }
    PUBLIC_PREFIXES = (
        "/static/",
        "/assets/",
    )

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 1. Allow public whitelisted paths
        if path in self.PUBLIC_PATHS or any(path.startswith(p) for p in self.PUBLIC_PREFIXES):
            return await call_next(request)

        # 2. Extract Authorization Bearer token
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            if ALLOW_LOCAL_DEV_SSO:
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
                try:
                    return await call_next(request)
                finally:
                    current_sso_user.reset(token_ctx)

            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required. Missing Authorization Bearer header."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:].strip()
        try:
            user = verify_sso_token(token)
            request.state.user = user
            token_ctx = current_sso_user.set(user)
            try:
                return await call_next(request)
            finally:
                current_sso_user.reset(token_ctx)
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
