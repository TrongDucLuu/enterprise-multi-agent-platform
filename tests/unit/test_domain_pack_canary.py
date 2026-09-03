import os
import pytest
from agent_core.app_utils.system_config import get_configured_systems, reload_system_config


def test_probe_active_pack():
    """
    Canary test verifying that the test runner environment actively respects DOMAIN_PACK
    without being overridden by hidden autouse fixtures.
    """
    reload_system_config()
    active_pack = os.getenv("DOMAIN_PACK", "it-helpdesk")
    configured_systems = get_configured_systems()

    if active_pack == "_template":
        assert "CORE" in configured_systems
        assert "ERP" not in configured_systems
    elif active_pack == "it-helpdesk":
        assert "ERP" in configured_systems
        assert "CRM" in configured_systems
        assert "HRM" in configured_systems
    else:
        assert len(configured_systems) > 0
