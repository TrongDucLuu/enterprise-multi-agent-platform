import datetime
import os
import logging
from typing import Optional
import jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
security_scheme = HTTPBearer(auto_error=False)

DEFAULT_JWT_SECRET = os.getenv("SSO_JWT_SECRET", "it-helpdesk-super-secure-dev-secret-key-2026")
DEFAULT_ISSUER = os.getenv("SSO_ISSUER", "https://accounts.google.com")
DEFAULT_AUDIENCE = os.getenv("SSO_CLIENT_ID", "it-helpdesk-agent-client-id")

class SSOUser(BaseModel):
    user_id: str = Field(description="Unique User ID / Sub")
    email: str = Field(description="Company Email")
    full_name: str = Field(default="Employee", description="Full Name")
    department: str = Field(default="General", description="Company Department")
    roles: list[str] = Field(default_factory=lambda: ["employee"], description="Assigned Roles")
    is_authenticated: bool = True

def create_sso_token(
    user: SSOUser,
    secret_key: str = DEFAULT_JWT_SECRET,
    expires_in_seconds: int = 3600,
    issuer: str = DEFAULT_ISSUER,
    audience: str = DEFAULT_AUDIENCE,
) -> str:
    """Creates a signed JWT token representing the authenticated SSO user."""
    now = datetime.datetime.now(datetime.timezone.utc)
    payload = {
        "sub": user.user_id,
        "email": user.email,
        "name": user.full_name,
        "department": user.department,
        "roles": user.roles,
        "iss": issuer,
        "aud": audience,
        "iat": int(now.timestamp()),
        "exp": int((now + datetime.timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    return jwt.encode(payload, secret_key, algorithm="HS256")

def verify_sso_token(
    token: str,
    secret_key: str = DEFAULT_JWT_SECRET,
    allowed_issuer: Optional[str] = None,
    allowed_audience: Optional[str] = None,
) -> SSOUser:
    """
    Validates a JWT token's signature, expiration, issuer, and audience.
    Returns the authenticated SSOUser object.
    """
    try:
        options = {
            "verify_signature": True,
            "verify_exp": True,
            "verify_iat": True,
            "verify_iss": bool(allowed_issuer),
            "verify_aud": bool(allowed_audience),
        }

        payload = jwt.decode(
            token,
            secret_key,
            algorithms=["HS256", "RS256"],
            issuer=allowed_issuer,
            audience=allowed_audience,
            options=options,
        )

        return SSOUser(
            user_id=payload.get("sub", payload.get("email", "unknown_user")),
            email=payload.get("email", "user@company.com"),
            full_name=payload.get("name", "Employee"),
            department=payload.get("department", "General"),
            roles=payload.get("roles", ["employee"]),
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
            detail=f"Invalid SSO Token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )

async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security_scheme),
) -> SSOUser:
    """
    FastAPI dependency that extracts and validates the Bearer SSO token from Authorization header.
    In local development, if ALLOW_LOCAL_DEV_SSO=true and no token is passed, returns a default dev user.
    """
    allow_dev_bypass = os.getenv("ALLOW_LOCAL_DEV_SSO", "true").lower() in ("true", "1")

    if not credentials:
        if allow_dev_bypass:
            logger.info("Using default local development SSO user context.")
            return SSOUser(
                user_id="dev-user-001",
                email="dev.employee@company.com",
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
