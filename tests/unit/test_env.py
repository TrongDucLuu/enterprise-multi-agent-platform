import os
import pytest
from unittest.mock import patch, MagicMock
from it_helpdesk_agent.app_utils.env import _fetch_secrets, init_environment

def test_fetch_secrets_from_local_environment(monkeypatch):
    monkeypatch.setenv("HELPDESK_ADMIN_API_KEY", "test_admin_key")
    monkeypatch.setenv("ERP_INTEGRATION_TOKEN", "test_erp_token")
    
    secrets = _fetch_secrets("test-project-123")
    assert secrets["HELPDESK_ADMIN_API_KEY"] == "test_admin_key"
    assert secrets["ERP_INTEGRATION_TOKEN"] == "test_erp_token"

@patch("google.cloud.secretmanager.SecretManagerServiceClient")
def test_fetch_secrets_from_secret_manager_fallback(mock_sm_client_cls, monkeypatch):
    for key in [
        "HELPDESK_ADMIN_API_KEY",
        "ERP_INTEGRATION_TOKEN",
        "HRM_INTEGRATION_TOKEN",
        "CRM_INTEGRATION_TOKEN",
        "SSO_CLIENT_SECRET",
        "SSO_JWT_SECRET",
    ]:
        monkeypatch.delenv(key, raising=False)

    mock_client = MagicMock()
    mock_sm_client_cls.return_value = mock_client

    mock_payload = MagicMock()
    mock_payload.data = b"secret_from_sm"
    mock_response = MagicMock(payload=mock_payload)
    mock_client.access_secret_version.return_value = mock_response

    secrets = _fetch_secrets("test-project-456")
    assert mock_client.access_secret_version.call_count == 6
    assert secrets["HELPDESK_ADMIN_API_KEY"] == "secret_from_sm"
    assert secrets["SSO_JWT_SECRET"] == "secret_from_sm"

@patch("vertexai.init")
@patch("it_helpdesk_agent.app_utils.env._fetch_secrets")
@patch("google.auth.default")
def test_init_environment(mock_google_auth, mock_fetch, mock_vertexai_init, monkeypatch):
    mock_google_auth.return_value = (None, "mock-helpdesk-project")
    mock_fetch.return_value = {"HELPDESK_ADMIN_API_KEY": "admin_val"}
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setenv("GOOGLE_CLOUD_REGION", "us-central1")

    project_id, model_loc, service_loc, secrets = init_environment()

    assert project_id == "mock-helpdesk-project"
    assert model_loc == "us-central1"
    assert service_loc == "us-central1"
    assert secrets == {"HELPDESK_ADMIN_API_KEY": "admin_val"}
    mock_vertexai_init.assert_called_once_with(project="mock-helpdesk-project", location="us-central1")
