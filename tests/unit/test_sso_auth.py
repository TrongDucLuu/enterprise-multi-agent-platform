import time
import pytest
from fastapi import HTTPException
from it_helpdesk_agent.app_utils.sso_auth import (
    SSOUser,
    create_sso_token,
    verify_sso_token,
    get_current_user,
    DEFAULT_JWT_SECRET,
)

def test_create_and_verify_valid_token():
    user = SSOUser(
        user_id="emp-1234",
        email="john.doe@company.com",
        full_name="John Doe",
        department="Finance",
        roles=["employee", "purchaser"]
    )
    token = create_sso_token(user, secret_key=DEFAULT_JWT_SECRET, expires_in_seconds=3600)
    assert isinstance(token, str)
    assert len(token) > 20

    verified_user = verify_sso_token(token, secret_key=DEFAULT_JWT_SECRET)
    assert verified_user.user_id == "emp-1234"
    assert verified_user.email == "john.doe@company.com"
    assert verified_user.full_name == "John Doe"
    assert verified_user.department == "Finance"
    assert "purchaser" in verified_user.roles
    assert verified_user.is_authenticated is True

def test_verify_expired_token():
    user = SSOUser(
        user_id="emp-5678",
        email="expired.user@company.com",
        full_name="Expired User",
        department="IT"
    )
    # Create token that expired 10 seconds ago
    token = create_sso_token(user, secret_key=DEFAULT_JWT_SECRET, expires_in_seconds=-10)

    with pytest.raises(HTTPException) as exc_info:
        verify_sso_token(token, secret_key=DEFAULT_JWT_SECRET)
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()

def test_verify_invalid_signature():
    user = SSOUser(
        user_id="emp-9999",
        email="hacker@company.com",
        full_name="Attacker",
        department="Unknown"
    )
    # Sign with a different secret key (>= 32 bytes)
    token = create_sso_token(user, secret_key="wrong-secret-key-123456789012345678901234")

    with pytest.raises(HTTPException) as exc_info:
        verify_sso_token(token, secret_key=DEFAULT_JWT_SECRET)
    assert exc_info.value.status_code == 401
    assert "invalid" in exc_info.value.detail.lower()

def test_verify_audience_and_issuer_constraints():
    user = SSOUser(
        user_id="emp-8888",
        email="alice@company.com",
        full_name="Alice Smith",
        department="HR"
    )
    token = create_sso_token(
        user,
        secret_key=DEFAULT_JWT_SECRET,
        issuer="https://custom.okta.com",
        audience="custom-app-id"
    )

    # Correct issuer and audience
    verified = verify_sso_token(
        token,
        secret_key=DEFAULT_JWT_SECRET,
        allowed_issuer="https://custom.okta.com",
        allowed_audience="custom-app-id"
    )
    assert verified.email == "alice@company.com"

    # Mismatched issuer
    with pytest.raises(HTTPException) as exc_info:
        verify_sso_token(
            token,
            secret_key=DEFAULT_JWT_SECRET,
            allowed_issuer="https://wrong.idp.com",
            allowed_audience="custom-app-id"
        )
    assert exc_info.value.status_code == 401

@pytest.mark.asyncio
async def test_get_current_user_local_dev_bypass(monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_DEV_SSO", "true")
    # No credentials passed
    user = await get_current_user(credentials=None)
    assert user.is_authenticated is True
    assert user.user_id == "dev-user-001"
    assert user.email == "dev.employee@company.com"

@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_credentials_in_prod(monkeypatch):
    monkeypatch.setenv("ALLOW_LOCAL_DEV_SSO", "false")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=None)
    assert exc_info.value.status_code == 401
    assert "missing" in exc_info.value.detail.lower()
