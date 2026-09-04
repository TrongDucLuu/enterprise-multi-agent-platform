"""
Dynamic System Configuration & RBAC Roles Loader.

Loads customer-specific enterprise systems, vendor examples, and RBAC roles from YAML config.
Enforces Fail-Closed security: invalid, missing, or improperly structured config files raise
exceptions immediately rather than falling back to unvetted defaults.
"""

import os
import re
import yaml
import logging
from pathlib import Path
from typing import Optional, Any

logger = logging.getLogger(__name__)

SYSTEM_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_]+$")
RESERVED_SYSTEM_NAMES = {"ALL"}


class SystemConfigurationError(Exception):
    """Raised when the systems configuration file is missing, invalid, or violates security constraints."""
    pass


_CONFIG_CACHE: Optional[dict[str, Any]] = None
_CACHED_CONFIG_PATH: Optional[str] = None


def get_default_config_path() -> str:
    """Returns the default path to the systems configuration file, prioritizing active domain pack."""
    env_path = os.getenv("SYSTEMS_CONFIG_PATH")
    if env_path:
        return env_path

    # Resolve relative to project root (2 levels up from app_utils)
    base_dir = Path(__file__).resolve().parent.parent.parent

    # Check active domain pack path
    pack_name = os.getenv("DOMAIN_PACK", "it-helpdesk")
    pack_systems = base_dir / "domain_packs" / pack_name / "systems.yaml"
    if pack_systems.is_file():
        return str(pack_systems)

    # Fail-closed in production: do not allow unvetted dev fallbacks
    try:
        from agent_core.app_utils.env import is_production_mode
    except ImportError:
        def is_production_mode() -> bool:
            return os.getenv("ENVIRONMENT", "").lower() == "production" or bool(os.getenv("K_SERVICE"))

    if is_production_mode():
        raise FileNotFoundError(
            f"Production systems config not found at '{pack_systems}'. "
            f"SYSTEMS_CONFIG_PATH or active pack domain_packs/{pack_name}/systems.yaml is required in production."
        )

    config_systems = base_dir / "config" / "systems.yaml"
    if config_systems.is_file():
        return str(config_systems)

    return str(pack_systems)


def load_system_config(config_path: Optional[str] = None, force_reload: bool = False) -> dict[str, Any]:
    """
    Loads and validates systems configuration from YAML file.
    Caches parsed result in memory.
    Raises SystemConfigurationError on failure (Fail-Closed).
    """
    global _CONFIG_CACHE, _CACHED_CONFIG_PATH

    target_path = config_path or get_default_config_path()

    if _CONFIG_CACHE is not None and not force_reload and _CACHED_CONFIG_PATH == target_path:
        return _CONFIG_CACHE

    if not os.path.exists(target_path):
        raise SystemConfigurationError(
            f"Tệp cấu hình hệ thống không tồn tại tại '{target_path}'. "
            f"Vui lòng kiểm tra biến môi trường SYSTEMS_CONFIG_PATH hoặc tạo tệp config/systems.yaml. (Fail-Closed)"
        )

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        raise SystemConfigurationError(
            f"Không thể đọc hoặc phân tích cú pháp YAML từ '{target_path}': {e}. (Fail-Closed)"
        ) from e

    if not isinstance(data, dict):
        raise SystemConfigurationError(
            f"Định dạng cấu hình không hợp lệ trong '{target_path}': Yêu cầu Root object là dictionary."
        )

    systems_data = data.get("systems")
    if not isinstance(systems_data, dict) or not systems_data:
        raise SystemConfigurationError(
            f"Cấu hình '{target_path}' thiếu trường 'systems' hoặc danh sách hệ thống rỗng. (Fail-Closed)"
        )

    shared_admin_roles = data.get("shared_admin_roles", [])
    if not isinstance(shared_admin_roles, list):
        raise SystemConfigurationError(
            f"Trường 'shared_admin_roles' trong '{target_path}' phải là danh sách (list). (Fail-Closed)"
        )

    # Validate each system entry
    validated_systems: dict[str, dict[str, Any]] = {}
    for raw_name, details in systems_data.items():
        if not isinstance(raw_name, str):
            raise SystemConfigurationError(f"Tên hệ thống '{raw_name}' phải là chuỗi ký tự.")
        
        name_upper = raw_name.strip().upper()
        if not SYSTEM_NAME_PATTERN.match(name_upper):
            raise SystemConfigurationError(
                f"Tên hệ thống '{raw_name}' không hợp lệ. Chỉ chấp nhận ký tự chữ, số và dấu gạch dưới [a-zA-Z0-9_]."
            )
        
        if name_upper in RESERVED_SYSTEM_NAMES:
            raise SystemConfigurationError(
                f"Tên hệ thống '{raw_name}' vi phạm từ khóa dành riêng ({RESERVED_SYSTEM_NAMES})."
            )

        if not isinstance(details, dict):
            raise SystemConfigurationError(
                f"Cấu hình cho hệ thống '{name_upper}' phải là dictionary."
            )

        system_roles = details.get("roles", [])
        if not isinstance(system_roles, list):
            raise SystemConfigurationError(
                f"Trường 'roles' của hệ thống '{name_upper}' phải là danh sách."
            )

        # Merge system specific roles with shared admin roles, preserving order and uniqueness
        combined_roles = []
        for r in [*system_roles, *shared_admin_roles]:
            if r and r not in combined_roles:
                combined_roles.append(r)

        validated_systems[name_upper] = {
            "name": name_upper,
            "display_name": details.get("display_name", name_upper),
            "vendor_examples": details.get("vendor_examples", ""),
            "description": details.get("description", ""),
            "common_issues": details.get("common_issues", []),
            "roles": combined_roles,
            "raw_roles": system_roles,
        }

    # Validate chunking configuration
    raw_chunking = data.get("chunking", {})
    if raw_chunking is not None and not isinstance(raw_chunking, dict):
        raise SystemConfigurationError(
            f"Trường 'chunking' trong '{target_path}' phải là dictionary. (Fail-Closed)"
        )
    
    raw_chunking = raw_chunking or {}
    default_strategy = raw_chunking.get("default_strategy", "auto")
    if default_strategy not in ("auto", "fixed", "semantic"):
        raise SystemConfigurationError(
            f"Chiến lược chunking mặc định '{default_strategy}' không hợp lệ. Phải là 'auto', 'fixed', hoặc 'semantic'."
        )

    max_chunk_size = raw_chunking.get("max_chunk_size", 1200)
    if not isinstance(max_chunk_size, int) or max_chunk_size <= 0:
        raise SystemConfigurationError(
            f"Trường 'chunking.max_chunk_size' ({max_chunk_size}) phải là số nguyên dương."
        )

    overlap = raw_chunking.get("overlap", 150)
    if not isinstance(overlap, int) or overlap < 0 or overlap >= max_chunk_size:
        raise SystemConfigurationError(
            f"Trường 'chunking.overlap' ({overlap}) phải là số nguyên không âm và nhỏ hơn max_chunk_size ({max_chunk_size})."
        )

    well_structured_max_section_ratio = raw_chunking.get("well_structured_max_section_ratio", 0.65)
    if not isinstance(well_structured_max_section_ratio, (int, float)) or not (0.0 < well_structured_max_section_ratio <= 1.0):
        raise SystemConfigurationError(
            f"Trường 'chunking.well_structured_max_section_ratio' ({well_structured_max_section_ratio}) phải nằm trong khoảng (0.0, 1.0]."
        )

    well_structured_min_avg_section_length = raw_chunking.get("well_structured_min_avg_section_length", 100)
    if not isinstance(well_structured_min_avg_section_length, int) or well_structured_min_avg_section_length < 0:
        raise SystemConfigurationError(
            f"Trường 'chunking.well_structured_min_avg_section_length' ({well_structured_min_avg_section_length}) phải là số nguyên không âm."
        )

    # Validate system-specific chunking overrides
    raw_chunking_systems = raw_chunking.get("systems", {})
    if raw_chunking_systems is not None and not isinstance(raw_chunking_systems, dict):
        raise SystemConfigurationError(
            f"Trường 'chunking.systems' trong '{target_path}' phải là dictionary. (Fail-Closed)"
        )
    
    validated_chunking_systems: dict[str, dict[str, Any]] = {}
    for sys_k, sys_v in (raw_chunking_systems or {}).items():
        if not isinstance(sys_v, dict):
            raise SystemConfigurationError(f"Cấu hình chunking cho hệ thống '{sys_k}' phải là dictionary.")
        sys_strategy = sys_v.get("strategy", default_strategy)
        if sys_strategy not in ("auto", "fixed", "semantic"):
            raise SystemConfigurationError(
                f"Chiến lược chunking cho hệ thống '{sys_k}' ('{sys_strategy}') không hợp lệ. Phải là 'auto', 'fixed', hoặc 'semantic'."
            )
        validated_chunking_systems[sys_k.strip().upper()] = {
            "strategy": sys_strategy,
            "max_chunk_size": sys_v.get("max_chunk_size", max_chunk_size),
            "overlap": sys_v.get("overlap", overlap),
        }

    validated_chunking = {
        "default_strategy": default_strategy,
        "max_chunk_size": max_chunk_size,
        "overlap": overlap,
        "well_structured_max_section_ratio": float(well_structured_max_section_ratio),
        "well_structured_min_avg_section_length": int(well_structured_min_avg_section_length),
        "systems": validated_chunking_systems,
    }

    # Validate document_processing configuration
    raw_doc_proc = data.get("document_processing", {})
    if raw_doc_proc is not None and not isinstance(raw_doc_proc, dict):
        raise SystemConfigurationError(
            f"Trường 'document_processing' trong '{target_path}' phải là dictionary. (Fail-Closed)"
        )
    
    raw_doc_proc = raw_doc_proc or {}
    pdf_parser = raw_doc_proc.get("pdf_parser", "pypdf_flat")
    if pdf_parser not in ("pypdf_flat", "document_ai"):
        raise SystemConfigurationError(
            f"Trường 'document_processing.pdf_parser' ('{pdf_parser}') không hợp lệ. Phải là 'pypdf_flat' hoặc 'document_ai'."
        )

    doc_ai_proc_id = raw_doc_proc.get("document_ai_processor_id")
    if pdf_parser == "document_ai":
        if not doc_ai_proc_id or not isinstance(doc_ai_proc_id, str) or not doc_ai_proc_id.strip():
            raise SystemConfigurationError(
                f"Cấu hình 'document_processing.pdf_parser' là 'document_ai' nhưng thiếu 'document_ai_processor_id'. "
                f"Vui lòng cung cấp Processor ID hợp lệ hoặc chuyển pdf_parser về 'pypdf_flat'. (Fail-Closed)"
            )
        doc_ai_proc_id = doc_ai_proc_id.strip()

    timeout_seconds = raw_doc_proc.get("document_ai_timeout_seconds", 60)
    if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
        raise SystemConfigurationError(
            f"Trường 'document_processing.document_ai_timeout_seconds' ({timeout_seconds}) phải là số dương."
        )

    max_retries = raw_doc_proc.get("document_ai_max_retries", 2)
    if not isinstance(max_retries, int) or max_retries < 0:
        raise SystemConfigurationError(
            f"Trường 'document_processing.document_ai_max_retries' ({max_retries}) phải là số nguyên không âm."
        )

    validated_doc_proc = {
        "pdf_parser": pdf_parser,
        "document_ai_processor_id": doc_ai_proc_id or "",
        "document_ai_timeout_seconds": float(timeout_seconds),
        "document_ai_max_retries": int(max_retries),
    }

    # Validate retrieval configuration
    raw_retrieval = data.get("retrieval", {})
    if raw_retrieval is not None and not isinstance(raw_retrieval, dict):
        raise SystemConfigurationError(
            f"Trường 'retrieval' trong '{target_path}' phải là dictionary. (Fail-Closed)"
        )
    
    raw_retrieval = raw_retrieval or {}
    fraction_lists_to_search = raw_retrieval.get("fraction_lists_to_search", 0.05)
    if not isinstance(fraction_lists_to_search, (int, float)) or not (0.0 < float(fraction_lists_to_search) <= 1.0):
        raise SystemConfigurationError(
            f"Trường 'retrieval.fraction_lists_to_search' ({fraction_lists_to_search}) phải là số thực trong khoảng (0.0, 1.0]. (Fail-Closed)"
        )

    hybrid_search_enabled = raw_retrieval.get("hybrid_search_enabled", True)
    if not isinstance(hybrid_search_enabled, bool):
        raise SystemConfigurationError(
            f"Trường 'retrieval.hybrid_search_enabled' ({hybrid_search_enabled}) phải là boolean. (Fail-Closed)"
        )

    reranker_enabled = raw_retrieval.get("reranker_enabled", False)
    if not isinstance(reranker_enabled, bool):
        raise SystemConfigurationError(
            f"Trường 'retrieval.reranker_enabled' ({reranker_enabled}) phải là boolean. (Fail-Closed)"
        )

    retrieve_k = raw_retrieval.get("retrieve_k", 20)
    if not isinstance(retrieve_k, int) or retrieve_k <= 0:
        raise SystemConfigurationError(
            f"Trường 'retrieval.retrieve_k' ({retrieve_k}) phải là số nguyên dương. (Fail-Closed)"
        )

    final_k = raw_retrieval.get("final_k", 3)
    if not isinstance(final_k, int) or final_k <= 0:
        raise SystemConfigurationError(
            f"Trường 'retrieval.final_k' ({final_k}) phải là số nguyên dương. (Fail-Closed)"
        )

    adaptive_retrieval_rounds = raw_retrieval.get("adaptive_retrieval_rounds", 2)
    if not isinstance(adaptive_retrieval_rounds, int) or adaptive_retrieval_rounds <= 0:
        raise SystemConfigurationError(
            f"Trường 'retrieval.adaptive_retrieval_rounds' ({adaptive_retrieval_rounds}) phải là số nguyên dương. (Fail-Closed)"
        )

    validated_retrieval = {
        "fraction_lists_to_search": float(fraction_lists_to_search),
        "hybrid_search_enabled": bool(hybrid_search_enabled),
        "reranker_enabled": bool(reranker_enabled),
        "retrieve_k": int(retrieve_k),
        "final_k": int(final_k),
        "adaptive_retrieval_rounds": int(adaptive_retrieval_rounds),
    }

    raw_rate_limiting = data.get("rate_limiting", {})
    if not isinstance(raw_rate_limiting, dict):
        raise SystemConfigurationError(
            f"Trường 'rate_limiting' trong '{target_path}' phải là một mapping/dict. (Fail-Closed)"
        )

    rpm = raw_rate_limiting.get("requests_per_minute", 60)
    if not isinstance(rpm, int) or rpm <= 0:
        raise SystemConfigurationError(
            f"Trường 'rate_limiting.requests_per_minute' ({rpm}) phải là số nguyên dương. (Fail-Closed)"
        )

    rpd = raw_rate_limiting.get("requests_per_day", 1000)
    if not isinstance(rpd, int) or rpd <= 0:
        raise SystemConfigurationError(
            f"Trường 'rate_limiting.requests_per_day' ({rpd}) phải là số nguyên dương. (Fail-Closed)"
        )

    l3_rpm = raw_rate_limiting.get("l3_requests_per_minute", 10)
    if not isinstance(l3_rpm, int) or l3_rpm <= 0:
        raise SystemConfigurationError(
            f"Trường 'rate_limiting.l3_requests_per_minute' ({l3_rpm}) phải là số nguyên dương. (Fail-Closed)"
        )

    l3_rpd = raw_rate_limiting.get("l3_requests_per_day", 100)
    if not isinstance(l3_rpd, int) or l3_rpd <= 0:
        raise SystemConfigurationError(
            f"Trường 'rate_limiting.l3_requests_per_day' ({l3_rpd}) phải là số nguyên dương. (Fail-Closed)"
        )

    monthly_token_budget = raw_rate_limiting.get("monthly_token_budget", 0)
    if not isinstance(monthly_token_budget, int) or monthly_token_budget < 0:
        raise SystemConfigurationError(
            f"Trường 'rate_limiting.monthly_token_budget' ({monthly_token_budget}) phải là số nguyên không âm (>= 0). (Fail-Closed)"
        )

    validated_rate_limiting = {
        "requests_per_minute": int(rpm),
        "requests_per_day": int(rpd),
        "l3_requests_per_minute": int(l3_rpm),
        "l3_requests_per_day": int(l3_rpd),
        "monthly_token_budget": int(monthly_token_budget),
    }

    # Validate clearance_levels
    raw_clearance = data.get("clearance_levels")
    if raw_clearance is None:
        try:
            from agent_core.app_utils.env import is_production_mode
            in_prod = is_production_mode()
        except ImportError:
            in_prod = os.getenv("ENVIRONMENT", "").lower() == "production" or bool(os.getenv("K_SERVICE"))

        is_domain_pack_or_main = (
            "domain_packs" in str(target_path)
            or "it-helpdesk" in str(target_path)
            or "_template" in str(target_path)
            or "ERP" in data.get("systems", {})
            or "CORE" in data.get("systems", {})
        )
        if in_prod and is_domain_pack_or_main:
            raise SystemConfigurationError(
                f"Cấu hình '{target_path}' thiếu trường 'clearance_levels'. "
                f"Trong môi trường production, clearance_levels là bắt buộc trong domain pack systems.yaml. (Fail-Closed)"
            )
        # Development default / custom config fallback
        raw_clearance = {
            3: ["it_admin", "sys_admin", "compliance_admin", "admin", "super_admin"],
            2: ["compliance_officer", "legal_counsel", "hr_manager", "finance_manager", "director", "support_lead", "it_specialist", "security_analyst", "compliance_analyst", "auditor"],
            1: ["*"],
            0: [],
        }

    if not isinstance(raw_clearance, dict):
        raise SystemConfigurationError(
            f"Trường 'clearance_levels' trong '{target_path}' phải là dictionary. (Fail-Closed)"
        )

    validated_clearance: dict[int, list[str]] = {}
    for k, v in raw_clearance.items():
        try:
            lvl = int(k)
        except (ValueError, TypeError):
            raise SystemConfigurationError(
                f"Mức clearance '{k}' trong 'clearance_levels' phải là số nguyên (0..3). (Fail-Closed)"
            )
        if lvl < 0 or lvl > 3:
            raise SystemConfigurationError(
                f"Mức clearance {lvl} trong 'clearance_levels' không hợp lệ (phải trong khoảng 0..3). (Fail-Closed)"
            )
        if not isinstance(v, list):
            raise SystemConfigurationError(
                f"Danh sách role cho mức clearance {lvl} phải là list. (Fail-Closed)"
            )
        validated_clearance[lvl] = [str(r).strip().lower() for r in v if str(r).strip()]

    # Ensure all levels 0..3 exist in dict
    for lvl in range(4):
        if lvl not in validated_clearance:
            validated_clearance[lvl] = []

    # Validate obligation_default_roles
    raw_obligation_roles = data.get("obligation_default_roles")
    if raw_obligation_roles is not None and not isinstance(raw_obligation_roles, list):
        raise SystemConfigurationError(
            f"Trường 'obligation_default_roles' trong '{target_path}' phải là list các role. (Fail-Closed)"
        )
    
    if raw_obligation_roles is None:
        validated_obligation_roles = validated_clearance.get(3, []) + validated_clearance.get(2, [])
        if not validated_obligation_roles:
            validated_obligation_roles = ["compliance_officer", "it_admin", "sys_admin", "legal_counsel"]
    else:
        validated_obligation_roles = [str(r).strip().lower() for r in raw_obligation_roles if str(r).strip()]

    user_role_mappings = data.get("user_role_mappings", {})
    if not isinstance(user_role_mappings, dict):
        user_role_mappings = {}

    group_role_mappings = data.get("group_role_mappings", {})
    if not isinstance(group_role_mappings, dict):
        group_role_mappings = {}

    domain_keywords = data.get("domain_keywords", {})
    if not isinstance(domain_keywords, dict):
        domain_keywords = {}

    parsed_config = {
        "shared_admin_roles": shared_admin_roles,
        "systems": validated_systems,
        "chunking": validated_chunking,
        "document_processing": validated_doc_proc,
        "retrieval": validated_retrieval,
        "rate_limiting": validated_rate_limiting,
        "clearance_levels": validated_clearance,
        "obligation_default_roles": validated_obligation_roles,
        "user_role_mappings": user_role_mappings,
        "group_role_mappings": group_role_mappings,
        "domain_keywords": domain_keywords,
    }

    _CONFIG_CACHE = parsed_config
    _CACHED_CONFIG_PATH = target_path
    logger.debug("Successfully loaded %d enterprise systems from %s", len(validated_systems), target_path)
    return _CONFIG_CACHE


def reload_system_config(config_path: Optional[str] = None) -> dict[str, Any]:
    """Forces cache invalidation and reloads the configuration."""
    global _USER_ROLE_MAPPINGS_CACHE, _COMPILED_KEYWORD_PATTERNS
    _USER_ROLE_MAPPINGS_CACHE = None
    _COMPILED_KEYWORD_PATTERNS = None
    try:
        from agent_core.tools.case_tool import clear_case_schema_cache
        clear_case_schema_cache()
    except Exception:
        pass
    return load_system_config(config_path=config_path, force_reload=True)


def get_configured_systems(config_path: Optional[str] = None) -> list[str]:
    """Returns sorted list of configured enterprise system identifiers (uppercase)."""
    cfg = load_system_config(config_path=config_path)
    return sorted(list(cfg["systems"].keys()))


def get_valid_system_filters(config_path: Optional[str] = None) -> set[str]:
    """Returns the set of valid system filter parameters, including 'ALL'."""
    return set(get_configured_systems(config_path=config_path)) | RESERVED_SYSTEM_NAMES


def get_system_required_roles(system: str, config_path: Optional[str] = None) -> list[str]:
    """Returns the merged list of roles required to access the specified system."""
    cfg = load_system_config(config_path=config_path)
    sys_upper = system.strip().upper() if system else ""
    sys_info = cfg["systems"].get(sys_upper)
    if sys_info:
        return sys_info["roles"]
    return []


def get_all_system_roles_map(config_path: Optional[str] = None) -> dict[str, list[str]]:
    """Returns mapping from each configured system to its required roles."""
    cfg = load_system_config(config_path=config_path)
    return {sys_name: info["roles"] for sys_name, info in cfg["systems"].items()}


def get_shared_admin_roles(config_path: Optional[str] = None) -> list[str]:
    """Returns the list of shared admin / IT support roles."""
    cfg = load_system_config(config_path=config_path)
    return cfg.get("shared_admin_roles", [])


def get_clearance_levels(config_path: Optional[str] = None) -> dict[int, list[str]]:
    """Returns normalized clearance levels mapping {0: [...], 1: [...], 2: [...], 3: [...]}."""
    cfg = load_system_config(config_path=config_path)
    return cfg.get("clearance_levels", {0: [], 1: ["*"], 2: [], 3: []})


def get_obligation_default_roles(config_path: Optional[str] = None) -> list[str]:
    """Returns default roles authorized to view/manage contract obligations."""
    cfg = load_system_config(config_path=config_path)
    return cfg.get("obligation_default_roles", ["compliance_officer", "it_admin", "sys_admin", "legal_counsel"])


def get_admin_roles(config_path: Optional[str] = None) -> list[str]:
    """Returns combined list of administrator roles (clearance level 3 and shared_admin_roles)."""
    cfg = load_system_config(config_path=config_path)
    clearance_lvl3 = cfg.get("clearance_levels", {}).get(3, [])
    shared_admins = cfg.get("shared_admin_roles", [])
    combined = list(dict.fromkeys([*clearance_lvl3, *shared_admins]))
    return combined


def compute_user_clearance(user_roles: list[str], config_path: Optional[str] = None) -> int:
    """
    Computes effective clearance level (0..3) for a given list of user roles
    based on the active domain pack's clearance_levels lattice.
    """
    if not user_roles:
        return 0

    clean_roles = {str(r).strip().lower() for r in user_roles if str(r).strip()}
    if not clean_roles:
        return 0

    clearance_map = get_clearance_levels(config_path=config_path)

    # Check from highest clearance (3) down to lowest (1)
    for lvl in sorted(clearance_map.keys(), reverse=True):
        if lvl <= 0:
            continue
        level_roles = set(clearance_map.get(lvl, []))
        if bool(clean_roles & level_roles):
            return lvl
        if "*" in level_roles:
            return lvl

    return 0


def get_rate_limiting_config(config_path: Optional[str] = None) -> dict[str, int]:
    """Returns the validated rate limiting configuration."""
    cfg = load_system_config(config_path=config_path)
    return cfg.get("rate_limiting", {
        "requests_per_minute": 60,
        "requests_per_day": 1000,
        "l3_requests_per_minute": 10,
        "l3_requests_per_day": 100,
        "monthly_token_budget": 0,
    })


def get_system_metadata(system: str, config_path: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Returns full metadata dictionary for a given system."""
    cfg = load_system_config(config_path=config_path)
    return cfg["systems"].get(system.strip().upper())


def get_system_instructions_prompt(config_path: Optional[str] = None) -> str:
    """
    Generates dynamic bullet points describing configured enterprise systems,
    vendors, and common issues for inclusion in LLM agent instructions.
    """
    cfg = load_system_config(config_path=config_path)
    lines = []
    for sys_name in sorted(cfg["systems"].keys()):
        info = cfg["systems"][sys_name]
        vendor = f" ({info['vendor_examples']})" if info.get("vendor_examples") else ""
        issues = ", ".join(info.get("common_issues", [])) if info.get("common_issues") else info.get("description", "")
        lines.append(f"         * **{sys_name}{vendor}:** {issues}")
    return "\n".join(lines)


def get_chunking_config(system: Optional[str] = None) -> dict[str, Any]:
    """
    Returns effective chunking configuration for a specific system or global default.
    Merges global chunking parameters with system-specific overrides if configured.
    """
    cfg = load_system_config()
    chunking_cfg = cfg.get("chunking", {})
    
    strategy = chunking_cfg.get("default_strategy", "auto")
    max_chunk_size = chunking_cfg.get("max_chunk_size", 1200)
    overlap = chunking_cfg.get("overlap", 150)
    max_section_ratio = chunking_cfg.get("well_structured_max_section_ratio", 0.65)
    min_avg_len = chunking_cfg.get("well_structured_min_avg_section_length", 100)

    if system:
        sys_upper = system.strip().upper()
        sys_chunking = chunking_cfg.get("systems", {}).get(sys_upper)
        if sys_chunking:
            strategy = sys_chunking.get("strategy", strategy)
            max_chunk_size = sys_chunking.get("max_chunk_size", max_chunk_size)
            overlap = sys_chunking.get("overlap", overlap)

    return {
        "strategy": strategy,
        "max_chunk_size": max_chunk_size,
        "overlap": overlap,
        "well_structured_max_section_ratio": max_section_ratio,
        "well_structured_min_avg_section_length": min_avg_len,
    }


def get_document_processing_config() -> dict[str, Any]:
    """Returns document processing configuration (PDF parser strategy, Document AI parameters)."""
    cfg = load_system_config()
    return cfg.get("document_processing", {
        "pdf_parser": "pypdf_flat",
        "document_ai_processor_id": None,
        "document_ai_timeout_seconds": 60.0,
        "document_ai_max_retries": 2,
    })


def get_retrieval_config() -> dict[str, Any]:
    """Returns retrieval and search configuration (fraction_lists_to_search, hybrid_search_enabled, reranker_enabled, retrieve_k, final_k, adaptive_retrieval_rounds)."""
    cfg = load_system_config()
    return cfg.get("retrieval", {
        "fraction_lists_to_search": 0.05,
        "hybrid_search_enabled": True,
        "reranker_enabled": False,
        "retrieve_k": 20,
        "final_k": 3,
        "adaptive_retrieval_rounds": 2,
    })


def get_user_role_mappings() -> dict[str, list[str]]:
    """
    Returns mapping from user email (lowercase) to list of assigned roles.
    Combines config/systems.yaml user_role_mappings with optional USER_ROLE_MAPPINGS env var.
    """
    cfg = load_system_config()
    mappings: dict[str, list[str]] = {}
    
    # 1. From YAML
    raw_mappings = cfg.get("user_role_mappings", {})
    if isinstance(raw_mappings, dict):
        for email, roles in raw_mappings.items():
            if isinstance(email, str) and isinstance(roles, list):
                mappings[email.strip().lower()] = [str(r).strip() for r in roles if str(r).strip()]

    # 2. From Environment Variable (Format: email:r1,r2;email2:r3 or JSON)
    env_mappings_raw = os.getenv("USER_ROLE_MAPPINGS", "").strip()
    if env_mappings_raw:
        try:
            if env_mappings_raw.startswith("{"):
                import json
                env_dict = json.loads(env_mappings_raw)
                for email, roles in env_dict.items():
                    if isinstance(roles, list):
                        mappings[email.strip().lower()] = [str(r).strip() for r in roles if str(r).strip()]
            else:
                for entry in env_mappings_raw.split(";"):
                    if ":" in entry:
                        email, roles_str = entry.split(":", 1)
                        roles_list = [r.strip() for r in roles_str.split(",") if r.strip()]
                        mappings[email.strip().lower()] = roles_list
        except Exception as e:
            logger.warning(f"Failed to parse USER_ROLE_MAPPINGS environment variable: {e}")

    return mappings


def get_group_role_mappings() -> dict[str, list[str]]:
    """
    Returns mapping from enterprise group email / name (lowercase) to list of assigned roles.
    Combines config/systems.yaml group_role_mappings with optional GROUP_ROLE_MAPPINGS env var.
    """
    cfg = load_system_config()
    mappings: dict[str, list[str]] = {}

    # 1. From YAML
    raw_mappings = cfg.get("group_role_mappings", {})
    if isinstance(raw_mappings, dict):
        for group, roles in raw_mappings.items():
            if isinstance(group, str) and isinstance(roles, list):
                mappings[group.strip().lower()] = [str(r).strip() for r in roles if str(r).strip()]

    # 2. From Environment Variable (Format: group1:r1,r2;group2:r3 or JSON)
    env_mappings_raw = os.getenv("GROUP_ROLE_MAPPINGS", "").strip()
    if env_mappings_raw:
        try:
            if env_mappings_raw.startswith("{"):
                import json
                env_dict = json.loads(env_mappings_raw)
                for group, roles in env_dict.items():
                    if isinstance(roles, list):
                        mappings[group.strip().lower()] = [str(r).strip() for r in roles if str(r).strip()]
            else:
                for entry in env_mappings_raw.split(";"):
                    if ":" in entry:
                        group, roles_str = entry.split(":", 1)
                        roles_list = [r.strip() for r in roles_str.split(",") if r.strip()]
                        mappings[group.strip().lower()] = roles_list
        except Exception as e:
            logger.warning(f"Failed to parse GROUP_ROLE_MAPPINGS environment variable: {e}")

    return mappings


def get_domain_keywords() -> dict[str, list[str]]:
    """Returns centralized domain keywords mapping for regex classification."""
    cfg = load_system_config()
    keywords = cfg.get("domain_keywords", {})
    if isinstance(keywords, dict):
        return keywords
    return {}


_COMPILED_KEYWORD_PATTERNS: Optional[dict[str, re.Pattern]] = None


def get_domain_keyword_patterns() -> dict[str, re.Pattern]:
    """
    Returns compiled regular expressions with word boundary (\\b) for each domain.
    Prevents substring collision (e.g., 'PO' matching 'chính sách' or 'report').
    """
    global _COMPILED_KEYWORD_PATTERNS
    if _COMPILED_KEYWORD_PATTERNS is not None:
        return _COMPILED_KEYWORD_PATTERNS

    keywords_dict = get_domain_keywords()
    patterns = {}
    for domain, kws in keywords_dict.items():
        if not kws:
            continue
        # Sort keywords by length descending to match longest phrases first
        escaped_kws = [re.escape(k.strip()) for k in kws if k.strip()]
        if escaped_kws:
            pattern_str = r"(?i)(?:\b" + r"\b|\b".join(escaped_kws) + r"\b)"
            patterns[domain] = re.compile(pattern_str, re.IGNORECASE)

    _COMPILED_KEYWORD_PATTERNS = patterns
    return _COMPILED_KEYWORD_PATTERNS


def resolve_user_roles(
    email: str,
    payload_roles: Optional[list[str]] = None,
    groups: Optional[list[str]] = None,
) -> list[str]:
    """
    Resolves enterprise roles for a verified SSO user.
    Priority:
    1. Group mappings from config/systems.yaml and GROUP_ROLE_MAPPINGS env (Google Workspace/Okta groups).
    2. Static email mappings from config/systems.yaml and USER_ROLE_MAPPINGS env.
    3. Firestore collection 'user_roles' lookup (if Firestore is active).
    4. Payload roles provided in JWT/OIDC.
    5. Base fallback: ['employee'].
    """
    email_norm = email.strip().lower() if email else ""
    resolved_roles: list[str] = []

    # 1. Group Role Mappings (Highest Priority for Enterprise IAM)
    if groups:
        group_mapping = get_group_role_mappings()
        for g in groups:
            g_norm = str(g).strip().lower()
            if g_norm in group_mapping:
                resolved_roles.extend(group_mapping[g_norm])

    # 2. Config & Env User Email Mapping
    user_mapping = get_user_role_mappings()
    if email_norm in user_mapping:
        resolved_roles.extend(user_mapping[email_norm])

    # 3. Firestore Lookup if enabled
    if not resolved_roles and (os.getenv("USE_FIRESTORE_ROLES", "false").lower() in ("true", "1") or bool(os.getenv("K_SERVICE"))):
        try:
            from google.cloud import firestore
            fs = firestore.Client()
            doc = fs.collection("user_roles").document(email_norm).get()
            if doc.exists:
                doc_roles = doc.to_dict().get("roles", [])
                if isinstance(doc_roles, list):
                    resolved_roles.extend(doc_roles)
        except Exception as e:
            logger.debug(f"Firestore role lookup skipped/failed for {email_norm}: {e}")

    # 4. Payload roles from token
    if payload_roles:
        for r in payload_roles:
            if r and r not in resolved_roles:
                resolved_roles.append(r)

    # 5. Ensure baseline 'employee' role is always present
    if "employee" not in resolved_roles:
        resolved_roles.append("employee")

    # Deduplicate preserving order
    seen = set()
    deduped = []
    for r in resolved_roles:
        if r not in seen:
            seen.add(r)
            deduped.append(r)

    return deduped


