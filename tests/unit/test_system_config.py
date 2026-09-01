import os
import tempfile
import pytest
import yaml

from agent_core.app_utils.system_config import (
    load_system_config,
    reload_system_config,
    get_configured_systems,
    get_valid_system_filters,
    get_system_required_roles,
    get_all_system_roles_map,
    get_shared_admin_roles,
    get_system_instructions_prompt,
    get_chunking_config,
    get_document_processing_config,
    get_retrieval_config,
    SystemConfigurationError,
)


def test_default_config_loads_erp_hrm_crm():
    reload_system_config()
    systems = get_configured_systems()
    assert "ERP" in systems
    assert "HRM" in systems
    assert "CRM" in systems

    filters = get_valid_system_filters()
    assert filters == {"ERP", "HRM", "CRM", "ALL"}

    shared_roles = get_shared_admin_roles()
    assert "it_admin" in shared_roles
    assert "support_agent" in shared_roles

    hrm_roles = get_system_required_roles("HRM")
    assert "hr_specialist" in hrm_roles
    assert "it_admin" in hrm_roles  # Merged shared admin role

    all_roles = get_all_system_roles_map()
    assert "ERP" in all_roles
    assert "HRM" in all_roles
    assert "CRM" in all_roles


def test_dynamic_system_addition_without_code_changes(monkeypatch):
    """Verifies that adding a new system (e.g. MES or HIS) is automatically picked up."""
    custom_yaml = {
        "shared_admin_roles": ["sysadmin", "it_support"],
        "systems": {
            "MES": {
                "display_name": "Manufacturing Execution System",
                "vendor_examples": "Siemens / Rockwell",
                "description": "Quản lý dây chuyền sản xuất",
                "common_issues": ["Lỗi kết nối PLC", "Tắc nghẽn SCADA"],
                "roles": ["factory_operator", "plant_manager"],
            },
            "HIS": {
                "display_name": "Hospital Information System",
                "vendor_examples": "Epic / Cerner",
                "description": "Quản lý bệnh án điện tử",
                "common_issues": ["Lỗi đồng bộ hồ sơ EMR", "Lỗi phân quyền bác sĩ"],
                "roles": ["doctor", "nurse", "chief_medical_officer"],
            }
        }
    }

    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(custom_yaml, f)
        temp_path = f.name

    try:
        monkeypatch.setenv("SYSTEMS_CONFIG_PATH", temp_path)
        reload_system_config(temp_path)

        systems = get_configured_systems()
        assert systems == ["HIS", "MES"]

        filters = get_valid_system_filters()
        assert filters == {"HIS", "MES", "ALL"}

        mes_roles = get_system_required_roles("MES")
        assert "factory_operator" in mes_roles
        assert "sysadmin" in mes_roles

        his_roles = get_system_required_roles("HIS")
        assert "doctor" in his_roles
        assert "it_support" in his_roles

        prompt = get_system_instructions_prompt()
        assert "MES (Siemens / Rockwell):" in prompt
        assert "HIS (Epic / Cerner):" in prompt
    finally:
        monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reload_system_config()


def test_system_config_fail_closed_missing_file(monkeypatch):
    monkeypatch.setenv("SYSTEMS_CONFIG_PATH", "/non/existent/path/to/systems.yaml")
    with pytest.raises(SystemConfigurationError, match="Tệp cấu hình hệ thống không tồn tại"):
        reload_system_config("/non/existent/path/to/systems.yaml")
    monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
    reload_system_config()


def test_system_config_fail_closed_invalid_yaml(monkeypatch):
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write("invalid: [yaml: unclosed bracket")
        temp_path = f.name

    try:
        monkeypatch.setenv("SYSTEMS_CONFIG_PATH", temp_path)
        with pytest.raises(SystemConfigurationError, match="Không thể đọc hoặc phân tích cú pháp YAML"):
            reload_system_config(temp_path)
    finally:
        monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reload_system_config()


def test_system_config_rejects_reserved_name_all(monkeypatch):
    custom_yaml = {
        "shared_admin_roles": ["admin"],
        "systems": {
            "ALL": {
                "display_name": "Reserved Name",
                "roles": ["user"]
            }
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(custom_yaml, f)
        temp_path = f.name

    try:
        monkeypatch.setenv("SYSTEMS_CONFIG_PATH", temp_path)
        with pytest.raises(SystemConfigurationError, match="vi phạm từ khóa dành riêng"):
            reload_system_config(temp_path)
    finally:
        monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reload_system_config()


def test_system_config_rejects_invalid_system_characters(monkeypatch):
    custom_yaml = {
        "shared_admin_roles": ["admin"],
        "systems": {
            "ERP-BAD!": {
                "display_name": "Bad characters",
                "roles": ["user"]
            }
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(custom_yaml, f)
        temp_path = f.name

    try:
        monkeypatch.setenv("SYSTEMS_CONFIG_PATH", temp_path)
        with pytest.raises(SystemConfigurationError, match="Chỉ chấp nhận ký tự chữ, số"):
            reload_system_config(temp_path)
    finally:
        monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reload_system_config()


def test_chunking_and_document_processing_config():
    from agent_core.app_utils.system_config import (
        get_chunking_config,
        get_document_processing_config,
    )
    reload_system_config()
    
    # Global default
    erp_chunk = get_chunking_config("ERP")
    assert erp_chunk["strategy"] == "auto"
    assert erp_chunk["max_chunk_size"] == 1200
    assert erp_chunk["overlap"] == 150
    assert erp_chunk["well_structured_max_section_ratio"] == 0.65
    assert erp_chunk["well_structured_min_avg_section_length"] == 100

    # System override (HRM configured as semantic in systems.yaml)
    hrm_chunk = get_chunking_config("HRM")
    assert hrm_chunk["strategy"] == "semantic"

    # Document processing default
    doc_proc = get_document_processing_config()
    assert doc_proc["pdf_parser"] == "pypdf_flat"
    assert doc_proc["document_ai_timeout_seconds"] == 60.0
    assert doc_proc["document_ai_max_retries"] == 2


def test_document_ai_missing_processor_id_fails_closed(monkeypatch):
    from agent_core.app_utils.system_config import get_document_processing_config
    bad_yaml = {
        "shared_admin_roles": ["admin"],
        "systems": {
            "ERP": {"roles": ["erp_user"]}
        },
        "document_processing": {
            "pdf_parser": "document_ai",
            "document_ai_processor_id": "",  # Empty -> Must Fail-Closed
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_yaml, f)
        temp_path = f.name

    try:
        monkeypatch.setenv("SYSTEMS_CONFIG_PATH", temp_path)
        with pytest.raises(SystemConfigurationError, match="thiếu 'document_ai_processor_id'"):
            reload_system_config(temp_path)
    finally:
        monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reload_system_config()


def test_invalid_chunking_strategy_fails_closed(monkeypatch):
    bad_yaml = {
        "shared_admin_roles": ["admin"],
        "systems": {
            "ERP": {"roles": ["erp_user"]}
        },
        "chunking": {
            "default_strategy": "invalid_strategy",
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_yaml, f)
        temp_path = f.name

    try:
        monkeypatch.setenv("SYSTEMS_CONFIG_PATH", temp_path)
        with pytest.raises(SystemConfigurationError, match="Chiến lược chunking mặc định 'invalid_strategy' không hợp lệ"):
            reload_system_config(temp_path)
    finally:
        monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reload_system_config()


def test_invalid_overlap_exceeding_max_chunk_size_fails_closed(monkeypatch):
    bad_yaml = {
        "shared_admin_roles": ["admin"],
        "systems": {
            "ERP": {"roles": ["erp_user"]}
        },
        "chunking": {
            "max_chunk_size": 500,
            "overlap": 600,  # Invalid: overlap >= max_chunk_size
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_yaml, f)
        temp_path = f.name

    try:
        monkeypatch.setenv("SYSTEMS_CONFIG_PATH", temp_path)
        with pytest.raises(SystemConfigurationError, match="nhỏ hơn max_chunk_size"):
            reload_system_config(temp_path)
    finally:
        monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reload_system_config()


def test_retrieval_config_defaults():
    reload_system_config()
    cfg = get_retrieval_config()
    assert cfg["fraction_lists_to_search"] == 0.05
    assert cfg["hybrid_search_enabled"] is True


def test_retrieval_config_invalid_fraction_fails_closed(monkeypatch):
    bad_yaml = {
        "shared_admin_roles": ["admin"],
        "systems": {
            "ERP": {"roles": ["erp_user"]}
        },
        "retrieval": {
            "fraction_lists_to_search": 1.5,  # Out of range (> 1.0)
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_yaml, f)
        temp_path = f.name

    try:
        monkeypatch.setenv("SYSTEMS_CONFIG_PATH", temp_path)
        with pytest.raises(SystemConfigurationError, match="fraction_lists_to_search"):
            reload_system_config(temp_path)
    finally:
        monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reload_system_config()


def test_retrieval_config_invalid_hybrid_fails_closed(monkeypatch):
    bad_yaml = {
        "shared_admin_roles": ["admin"],
        "systems": {
            "ERP": {"roles": ["erp_user"]}
        },
        "retrieval": {
            "hybrid_search_enabled": "not_a_bool",
        }
    }
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.dump(bad_yaml, f)
        temp_path = f.name

    try:
        monkeypatch.setenv("SYSTEMS_CONFIG_PATH", temp_path)
        with pytest.raises(SystemConfigurationError, match="hybrid_search_enabled"):
            reload_system_config(temp_path)
    finally:
        monkeypatch.delenv("SYSTEMS_CONFIG_PATH", raising=False)
        if os.path.exists(temp_path):
            os.remove(temp_path)
        reload_system_config()


