import base64
import json
import pytest
import jwt as pyjwt
from unittest.mock import MagicMock, patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from it_helpdesk_agent.app_utils.sso_auth import (
    SSOUser,
    create_dev_mock_token,
    verify_google_oidc_token,
    verify_dev_mock_token,
    verify_sso_token,
    get_current_user,
    get_current_sso_user,
    require_role,
    current_sso_user,
    SSOAuthenticationMiddleware,
    DEV_JWT_SECRET,
    SSO_CLIENT_ID,
)


def _make_dummy_rs256_jwt(payload: dict) -> str:
    h = base64.urlsafe_b64encode(json.dumps({"alg": "RS256", "typ": "JWT"}).encode()).decode().rstrip("=")
    p = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"{h}.{p}.dummy_signature"


def test_verify_google_oidc_token_valid():
    mock_payload = {
        "sub": "google-uid-12345",
        "email": "employee@company.com",
        "email_verified": True,
        "name": "Jane Enterprise",
        "department": "Engineering",
        "hd": "company.com",
        "iss": "https://accounts.google.com",
        "aud": SSO_CLIENT_ID,
    }

    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=mock_payload):
        user = verify_google_oidc_token(
            token="dummy.google.rs256.token",
            client_id=SSO_CLIENT_ID,
            allowed_domains=["company.com"],
        )
        assert user.user_id == "google-uid-12345"
        assert user.email == "employee@company.com"
        assert user.full_name == "Jane Enterprise"
        assert user.hosted_domain == "company.com"
        assert user.is_authenticated is True


def test_verify_google_oidc_token_prod_fails_closed_if_no_domains(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.IS_PRODUCTION", True)
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOWED_DOMAINS", [])

    with pytest.raises(HTTPException) as exc_info:
        verify_google_oidc_token(token="dummy.token", allowed_domains=[])
    assert exc_info.value.status_code == 500
    assert "ALLOWED_DOMAINS" in exc_info.value.detail


def test_verify_google_oidc_token_unverified_email():
    mock_payload = {
        "sub": "google-uid-12345",
        "email": "unverified@company.com",
        "email_verified": False,
        "iss": "https://accounts.google.com",
        "aud": SSO_CLIENT_ID,
    }

    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=mock_payload):
        with pytest.raises(HTTPException) as exc_info:
            verify_google_oidc_token(token="dummy.token", allowed_domains=["company.com"])
        assert exc_info.value.status_code == 401
        assert "not verified" in exc_info.value.detail.lower()


def test_verify_google_oidc_token_invalid_issuer():
    mock_payload = {
        "sub": "google-uid-12345",
        "email": "employee@company.com",
        "email_verified": True,
        "iss": "https://untrusted-idp.attacker.com",
        "aud": SSO_CLIENT_ID,
    }

    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=mock_payload):
        with pytest.raises(HTTPException) as exc_info:
            verify_google_oidc_token(token="dummy.token", allowed_domains=["company.com"])
        assert exc_info.value.status_code == 401
        assert "invalid oidc token issuer" in exc_info.value.detail.lower()


def test_verify_google_oidc_token_domain_restriction():
    mock_payload_foreign = {
        "sub": "google-uid-9999",
        "email": "personal@gmail.com",
        "email_verified": True,
        "hd": "gmail.com",
        "iss": "https://accounts.google.com",
        "aud": SSO_CLIENT_ID,
    }

    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=mock_payload_foreign):
        with pytest.raises(HTTPException) as exc_info:
            verify_google_oidc_token(
                token="dummy.token",
                allowed_domains=["company.com"],
            )
        assert exc_info.value.status_code == 403
        assert "domain not permitted" in exc_info.value.detail.lower()


def test_algorithm_confusion_prevention_hs256_in_prod(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)

    # Attacker attempts to sign with raw HS256 algorithm
    raw_forged_token = pyjwt.encode(
        {"sub": "attacker", "email": "attacker@evil.com"},
        "some-random-secret-key-1234567890123456",
        algorithm="HS256",
    )

    with pytest.raises(HTTPException) as exc_info:
        verify_sso_token(raw_forged_token)
    assert exc_info.value.status_code == 401
    assert "prohibited in production mode" in exc_info.value.detail.lower()


def test_create_and_verify_dev_mock_token(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", True)

    user = SSOUser(
        user_id="emp-1234",
        email="john.doe@company.com",
        full_name="John Doe",
        department="Finance",
        roles=["employee", "purchaser"],
    )
    token = create_dev_mock_token(user, secret_key=DEV_JWT_SECRET, expires_in_seconds=3600)
    assert isinstance(token, str)

    verified_user = verify_dev_mock_token(token, secret_key=DEV_JWT_SECRET)
    assert verified_user.user_id == "emp-1234"
    assert verified_user.email == "john.doe@company.com"
    assert verified_user.full_name == "John Doe"
    assert verified_user.department == "Finance"
    assert "purchaser" in verified_user.roles
    assert verified_user.is_authenticated is True


def test_verify_expired_dev_mock_token(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", True)

    user = SSOUser(
        user_id="emp-5678",
        email="expired.user@company.com",
        full_name="Expired User",
        department="IT",
    )
    token = create_dev_mock_token(user, secret_key=DEV_JWT_SECRET, expires_in_seconds=-10)

    with pytest.raises(HTTPException) as exc_info:
        verify_dev_mock_token(token, secret_key=DEV_JWT_SECRET)
    assert exc_info.value.status_code == 401
    assert "expired" in exc_info.value.detail.lower()


def test_verify_invalid_signature_dev_mock_token(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", True)

    user = SSOUser(
        user_id="emp-9999",
        email="hacker@company.com",
        full_name="Attacker",
        department="Unknown",
    )
    token = create_dev_mock_token(user, secret_key="wrong-secret-key-123456789012345678901234")

    with pytest.raises(HTTPException) as exc_info:
        verify_dev_mock_token(token, secret_key=DEV_JWT_SECRET)
    assert exc_info.value.status_code == 401
    assert "invalid" in exc_info.value.detail.lower()


def test_require_role_rbac():
    user_admin = SSOUser(
        user_id="admin-1",
        email="admin@company.com",
        roles=["employee", "it_admin"]
    )
    token_ctx = current_sso_user.set(user_admin)
    try:
        allowed, err = require_role(["it_admin"])
        assert allowed is True
        assert err is None

        allowed, err = require_role(["finance_director"])
        assert allowed is False
        assert "không đủ" in err
    finally:
        current_sso_user.reset(token_ctx)


def test_sso_authentication_middleware_protects_agent_endpoints(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)

    test_app = FastAPI()
    test_app.add_middleware(SSOAuthenticationMiddleware)

    @test_app.get("/healthz")
    def health_check():
        return {"status": "ok"}

    @test_app.post("/run")
    def run_agent():
        user = get_current_sso_user()
        return {"response": "agent output", "user_email": user.email if user else None}

    client = TestClient(test_app)

    # 1. Public route succeeds without header
    res_health = client.get("/healthz")
    assert res_health.status_code == 200

    # 2. Protected agent endpoint blocked without header
    res_unauth = client.post("/run")
    assert res_unauth.status_code == 401
    assert "Missing Authorization Bearer" in res_unauth.json()["detail"]

    # 3. Protected agent endpoint succeeds with valid Bearer token and propagates ContextVar
    mock_payload = {
        "sub": "user-valid",
        "email": "user@company.com",
        "email_verified": True,
        "name": "Jane Employee",
        "iss": "https://accounts.google.com",
        "aud": SSO_CLIENT_ID,
    }
    with patch("google.oauth2.id_token.verify_oauth2_token", return_value=mock_payload):
        sample_rs256_token = _make_dummy_rs256_jwt({"sub": "user-valid", "iss": "https://accounts.google.com"})
        res_auth = client.post("/run", headers={"Authorization": f"Bearer {sample_rs256_token}"})
        assert res_auth.status_code == 200
        assert res_auth.json()["response"] == "agent output"
        assert res_auth.json()["user_email"] == "user@company.com"


@pytest.mark.asyncio
async def test_get_current_user_local_dev_bypass(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", True)
    user = await get_current_user(credentials=None)
    assert user.is_authenticated is True
    assert user.user_id == "dev-user-001"
    assert user.email == "dev.employee@company.com"


@pytest.mark.asyncio
async def test_get_current_user_rejects_missing_credentials_in_prod(monkeypatch):
    monkeypatch.setattr("it_helpdesk_agent.app_utils.sso_auth.ALLOW_LOCAL_DEV_SSO", False)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(credentials=None)
    assert exc_info.value.status_code == 401
    assert "missing" in exc_info.value.detail.lower()
