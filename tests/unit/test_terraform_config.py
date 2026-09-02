"""
Unit tests for Terraform HCL configuration and security defaults.
Asserts safe production defaults, mandatory zero-trust variables, and validation check blocks.
"""
import re
from pathlib import Path


def _get_terraform_dir() -> Path:
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "deployment" / "terraform"


def test_terraform_required_variables_have_no_defaults():
    """Asserts that critical security variables (sso_client_id, allowed_domains) are strictly required with no defaults."""
    tf_dir = _get_terraform_dir()
    variables_file = tf_dir / "variables.tf"
    assert variables_file.exists(), f"variables.tf not found at {variables_file}"

    content = variables_file.read_text(encoding="utf-8")

    # Match variable blocks
    var_blocks = re.findall(r'variable\s+"([^"]+)"\s+\{([^}]+)\}', content, re.DOTALL)
    var_dict = {name: body for name, body in var_blocks}

    assert "sso_client_id" in var_dict
    assert "default" not in var_dict["sso_client_id"], "sso_client_id must be a mandatory variable without default!"

    assert "allowed_domains" in var_dict
    assert "default" not in var_dict["allowed_domains"], "allowed_domains must be a mandatory variable without default!"

    assert "project_id" in var_dict
    assert "default" not in var_dict["project_id"], "project_id must be a mandatory variable without default!"


def test_terraform_safe_defaults():
    """Asserts that default values adhere to least privilege, low idle cost, and BigQuery production backend."""
    tf_dir = _get_terraform_dir()
    variables_file = tf_dir / "variables.tf"
    content = variables_file.read_text(encoding="utf-8")

    # knowledge_backend default must be bigquery
    kb_match = re.search(r'variable\s+"knowledge_backend"\s+\{([^}]+)\}', content, re.DOTALL)
    assert kb_match, "knowledge_backend variable not found"
    assert 'default     = "bigquery"' in kb_match.group(1) or 'default = "bigquery"' in kb_match.group(1)

    # allow_unauthenticated default must be false
    auth_match = re.search(r'variable\s+"allow_unauthenticated"\s+\{([^}]+)\}', content, re.DOTALL)
    assert auth_match, "allow_unauthenticated variable not found"
    assert 'default     = false' in auth_match.group(1) or 'default = false' in auth_match.group(1)

    # environment default must be development
    env_match = re.search(r'variable\s+"environment"\s+\{([^}]+)\}', content, re.DOTALL)
    assert env_match, "environment variable not found"
    assert 'default     = "development"' in env_match.group(1) or 'default = "development"' in env_match.group(1)

    # min_instance_count default must be 0 (scale to zero)
    min_inst_match = re.search(r'variable\s+"min_instance_count"\s+\{([^}]+)\}', content, re.DOTALL)
    assert min_inst_match, "min_instance_count variable not found"
    assert 'default     = 0' in min_inst_match.group(1) or 'default = 0' in min_inst_match.group(1)

    # domain_pack variable exists with default "it-helpdesk"
    pack_match = re.search(r'variable\s+"domain_pack"\s+\{([^}]+)\}', content, re.DOTALL)
    assert pack_match, "domain_pack variable not found"
    assert 'default     = "it-helpdesk"' in pack_match.group(1) or 'default = "it-helpdesk"' in pack_match.group(1)


def test_terraform_check_blocks_and_validations():
    """Asserts that main.tf contains production guardrail checks and environment validations."""
    tf_dir = _get_terraform_dir()
    main_file = tf_dir / "main.tf"
    assert main_file.exists(), f"main.tf not found at {main_file}"
    content = main_file.read_text(encoding="utf-8")

    assert 'check "production_knowledge_backend"' in content
    assert 'check "production_allowed_domains"' in content
    assert 'check "production_edge_security"' in content
    assert 'name  = "DOMAIN_PACK"' in content


def test_terraform_tfvars_example_exists_and_complete():
    """Asserts that terraform.tfvars.example provides a complete template for customer onboarding."""
    tf_dir = _get_terraform_dir()
    example_file = tf_dir / "terraform.tfvars.example"
    assert example_file.exists(), f"terraform.tfvars.example not found at {example_file}"

    content = example_file.read_text(encoding="utf-8")
    assert "project_id" in content
    assert "sso_client_id" in content
    assert "allowed_domains" in content
    assert "domain_pack" in content
    assert "ai_assets_bucket" in content
    assert "allowed_artifact_bucket" in content
