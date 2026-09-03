import os
import json
import subprocess
import sys
import hashlib
import pytest
from unittest.mock import patch, MagicMock

# Pins it-helpdesk pack because tests assert IT Helpdesk groups (it-admins, it-support) and systems (ERP, HRM, CRM)
pytestmark = pytest.mark.usefixtures("pinned_it_helpdesk_pack")

from agent_core.app_utils.system_config import (
    resolve_user_roles,
    get_user_role_mappings,
    reload_system_config,
)
from agent_core.app_utils.sso_auth import verify_google_oidc_token, SSOUser


class TestRBACProvisioning:
    """
    Comprehensive regression-proofing test suite for dynamic enterprise role provisioning.
    Protects P0.1 against regressions in priority hierarchy.
    """

    @pytest.fixture(autouse=True)
    def clean_environment(self, monkeypatch):
        """Ensures isolated clean environment and reloaded config for each test."""
        monkeypatch.delenv("USER_ROLE_MAPPINGS", raising=False)
        monkeypatch.delenv("USE_FIRESTORE_ROLES", raising=False)
        reload_system_config()
        yield
        monkeypatch.delenv("USER_ROLE_MAPPINGS", raising=False)
        monkeypatch.delenv("USE_FIRESTORE_ROLES", raising=False)
        reload_system_config()

    def test_role_resolution_from_yaml_mapping(self, monkeypatch):
        """Test Priority 1a: Email present in YAML user_role_mappings."""
        mock_yaml_mappings = {
            "admin@company.com": ["it_admin", "sys_admin"],
            "finance.lead@company.com": ["finance_user", "accountant"],
        }
        with patch("agent_core.app_utils.system_config.get_user_role_mappings", return_value=mock_yaml_mappings):
            admin_roles = resolve_user_roles("admin@company.com")
            assert "it_admin" in admin_roles
            assert "sys_admin" in admin_roles
            assert "employee" in admin_roles

            finance_roles = resolve_user_roles("finance.lead@company.com")
            assert "finance_user" in finance_roles
            assert "accountant" in finance_roles
            assert "employee" in finance_roles

    def test_role_resolution_from_env_string_format(self, monkeypatch):
        """Test Priority 1b: Email present in USER_ROLE_MAPPINGS env var (colon-separated format)."""
        monkeypatch.setenv(
            "USER_ROLE_MAPPINGS",
            "custom.admin@corp.io:secops,it_admin;custom.hr@corp.io:hrm_lead"
        )
        reload_system_config()

        admin_roles = resolve_user_roles("custom.admin@corp.io")
        assert "secops" in admin_roles
        assert "it_admin" in admin_roles
        assert "employee" in admin_roles

        hr_roles = resolve_user_roles("custom.hr@corp.io")
        assert "hrm_lead" in hr_roles
        assert "employee" in hr_roles

    def test_role_resolution_from_env_json_format(self, monkeypatch):
        """Test Priority 1b: Email present in USER_ROLE_MAPPINGS env var (JSON format)."""
        json_mapping = {
            "developer@cloud.org": ["devops", "it_admin"],
            "finance_auditor@cloud.org": ["erp_finance", "audit"]
        }
        monkeypatch.setenv("USER_ROLE_MAPPINGS", json.dumps(json_mapping))
        reload_system_config()

        dev_roles = resolve_user_roles("developer@cloud.org")
        assert "devops" in dev_roles
        assert "it_admin" in dev_roles
        assert "employee" in dev_roles

        audit_roles = resolve_user_roles("finance_auditor@cloud.org")
        assert "erp_finance" in audit_roles
        assert "audit" in audit_roles
        assert "employee" in audit_roles

    def test_role_resolution_from_firestore_lookup(self, monkeypatch):
        """Test Priority 2: Fallback to Firestore user_roles collection when not in YAML/env."""
        monkeypatch.setenv("USE_FIRESTORE_ROLES", "true")

        mock_doc = MagicMock()
        mock_doc.exists = True
        mock_doc.to_dict.return_value = {"roles": ["crm_sales", "cloud_admin"]}

        mock_collection = MagicMock()
        mock_collection.document.return_value.get.return_value = mock_doc

        mock_fs_client = MagicMock()
        mock_fs_client.collection.return_value = mock_collection

        mock_fs_mod = MagicMock()
        mock_fs_mod.Client.return_value = mock_fs_client

        with patch.dict("sys.modules", {"google.cloud.firestore": mock_fs_mod}):
            roles = resolve_user_roles("sales.rep@partner.com")
            assert "crm_sales" in roles
            assert "cloud_admin" in roles
            assert "employee" in roles
            mock_collection.document.assert_called_with("sales.rep@partner.com")

    def test_role_resolution_fallback_to_default_employee(self):
        """Test Priority 4: Unknown/unmapped user defaults to ['employee']."""
        roles = resolve_user_roles("unknown.contractor@partner.com")
        assert roles == ["employee"]

    def test_role_resolution_payload_roles_merging(self, monkeypatch):
        """Test Priority 3: Payload roles from JWT/OIDC are appended and merged without overwriting."""
        monkeypatch.setenv("USER_ROLE_MAPPINGS", "alice@corp.com:secops")
        reload_system_config()

        # Alice has static role 'secops', token payload provides 'extra_role'
        roles = resolve_user_roles("alice@corp.com", payload_roles=["extra_role", "secops"])
        assert "secops" in roles
        assert "extra_role" in roles
        assert "employee" in roles
        # Check deduplication
        assert roles.count("secops") == 1
        assert roles.count("employee") == 1

    def test_role_resolution_from_group_mapping(self, monkeypatch):
        """Test Priority 1a: Enterprise group in YAML group_role_mappings."""
        # config/systems.yaml maps 'gcp-it-admins@company.com' -> ['it_admin', 'sys_admin']
        roles = resolve_user_roles(
            "new.engineer@company.com",
            groups=["gcp-it-admins@company.com"]
        )
        assert "it_admin" in roles
        assert "sys_admin" in roles
        assert "employee" in roles

    def test_role_resolution_from_env_group_mapping(self, monkeypatch):
        """Test Priority 1b: Enterprise group in GROUP_ROLE_MAPPINGS env var."""
        monkeypatch.setenv(
            "GROUP_ROLE_MAPPINGS",
            "secops-squad@company.com:security_officer,audit;devops-core@company.com:sys_admin"
        )
        reload_system_config()

        roles = resolve_user_roles(
            "dev@company.com",
            groups=["secops-squad@company.com"]
        )
        assert "security_officer" in roles
        assert "audit" in roles
        assert "employee" in roles

    def test_verify_google_oidc_token_integrates_resolve_user_roles(self, monkeypatch):
        """
        End-to-End integration test: verify_google_oidc_token uses resolve_user_roles
        to accurately construct SSOUser with enterprise roles.
        """
        monkeypatch.setenv("SSO_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
        monkeypatch.setenv("ALLOWED_DOMAINS", "enterprise.com,company.com")
        monkeypatch.setenv("USER_ROLE_MAPPINGS", "chief_admin@enterprise.com:it_admin,secops")
        reload_system_config()

        mock_payload = {
            "sub": "112233445566778899",
            "email": "chief_admin@enterprise.com",
            "email_verified": True,
            "name": "Chief Administrator",
            "hd": "enterprise.com",
            "department": "Infrastructure",
            "iss": "https://accounts.google.com",
            "aud": "test-client-id.apps.googleusercontent.com",
        }

        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=mock_payload):
            sso_user = verify_google_oidc_token("mock-valid-google-id-token")
            assert isinstance(sso_user, SSOUser)
            assert sso_user.user_id == "112233445566778899"
            assert sso_user.email == "chief_admin@enterprise.com"
            assert "it_admin" in sso_user.roles
            assert "secops" in sso_user.roles
            assert "employee" in sso_user.roles
            assert sso_user.is_authenticated is True

    def test_verify_google_oidc_token_with_groups(self, monkeypatch):
        """
        Integration test: verify_google_oidc_token extracts groups from payload
        and resolves roles via group_role_mappings.
        """
        monkeypatch.setenv("SSO_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
        monkeypatch.setenv("ALLOWED_DOMAINS", "company.com")
        monkeypatch.setenv("GROUP_ROLE_MAPPINGS", "gcp-secops@company.com:it_admin,security_officer")
        reload_system_config()

        mock_payload = {
            "sub": "889900112233",
            "email": "analyst@company.com",
            "email_verified": True,
            "name": "Security Analyst",
            "hd": "company.com",
            "groups": ["gcp-secops@company.com"],
            "iss": "https://accounts.google.com",
            "aud": "test-client-id.apps.googleusercontent.com",
        }

        with patch("google.oauth2.id_token.verify_oauth2_token", return_value=mock_payload):
            sso_user = verify_google_oidc_token("mock-valid-google-id-token")
            assert isinstance(sso_user, SSOUser)
            assert sso_user.email == "analyst@company.com"
            assert "security_officer" in sso_user.roles
            assert "it_admin" in sso_user.roles
            assert sso_user.groups == ["gcp-secops@company.com"]


class TestHashStability:
    """
    Regression-proofing test suite for SHA-256 process stability (P0.3).
    Guarantees cross-process deterministic key derivation regardless of PYTHONHASHSEED.
    """

    def test_sha256_constant_hex_deterministic(self):
        """Verifies that hashlib.sha256 produces exact expected hexadecimal hashes."""
        test_inputs = {
            "user:123456": hashlib.sha256(b"user:123456").hexdigest(),
            "user:it_admin@company.com": hashlib.sha256(b"user:it_admin@company.com").hexdigest(),
            "public:cách kết nối wifi văn phòng": hashlib.sha256("public:cách kết nối wifi văn phòng".encode("utf-8")).hexdigest(),
        }
        # Hardcoded verification against known sha256 golden values
        assert test_inputs["user:123456"] == "67eabbf39d1e39ae7fad930244949c85d12b72965795794c9d5b66e8d8595467"
        assert test_inputs["user:it_admin@company.com"] == "101d391f1bcb9fd485cb1ccd2d99ec5ab66586f68882093879b6aa8b7b9c78bb"
        assert test_inputs["public:cách kết nối wifi văn phòng"] == "4cb075b88852db77bf95180446096074f9d4f8a97af468f4c448545e3ab90f64"

        for raw_str, expected_hex in test_inputs.items():
            computed = hashlib.sha256(raw_str.encode("utf-8")).hexdigest()
            assert computed == expected_hex, f"Hash mismatch for {raw_str}: expected {expected_hex}, got {computed}"

    def test_sha256_subprocess_hashseed_invariance(self):
        """
        Executes sub-processes with PYTHONHASHSEED=0 and PYTHONHASHSEED=random
        to prove sha256 output is 100% invariant across Python runtimes.
        """
        code = (
            "import hashlib\n"
            "user_id = 'user_corp_9988'\n"
            "h = hashlib.sha256(user_id.encode('utf-8')).hexdigest()[:32]\n"
            "print(h)\n"
        )

        env0 = dict(os.environ, PYTHONHASHSEED="0")
        env_rand = dict(os.environ, PYTHONHASHSEED="random")

        res0 = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env0,
            check=True
        )

        res_rand = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env_rand,
            check=True
        )

        assert res0.stdout.strip() == res_rand.stdout.strip()
        assert res0.stdout.strip() == hashlib.sha256(b"user_corp_9988").hexdigest()[:32]
