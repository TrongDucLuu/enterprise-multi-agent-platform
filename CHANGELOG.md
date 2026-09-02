# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.1.0] - 2026-09-02

### Added
- **Fail-Closed & Dynamic Pack Architecture (Phần A)**:
  - Lazy-loaded sample knowledge and obligations from active domain pack YAMLs (`knowledge.yaml`, `obligations.yaml`) avoiding memory-leak imports.
  - Fail-closed system configuration resolution (`SYSTEMS_CONFIG_PATH`) with strict validation.
  - Subprocess clean boot and AST zero-hardcode anti-regression test harness (`test_zero_hardcode_parity.py`).
- **Infrastructure & Terraform Blocking Preconditions (Phần B)**:
  - Added strict variable validation regex for `domain_pack` and `allowed_domains`.
  - Added Cloud Run `lifecycle { precondition }` blocks preventing insecure production deployments.
  - Hardened Redis Memorystore with `auth_enabled = true`, in-transit TLS encryption, and secure container environment injection (`REDIS_AUTH_STRING`, `REDIS_USE_TLS`).
- **Case Lifecycle State Machine & RBAC (Phần C)**:
  - Enforced deterministic status transition whitelist (`Open -> In_Progress -> Resolved -> Closed`) and terminal state immutability.
  - Enforced Role-Based Access Control (RBAC) on `route_case_to_tier` (rejecting unprivileged escalation to L2/L3).
- **Firestore Fail-Closed, OCC & Audit History (Phần D)**:
  - Enforced fail-closed persistence in production mode (`RuntimeError` on connection loss, no silent in-memory fallback).
  - Optimistic Concurrency Control (OCC) with incremental version counters and 409 conflict detection.
  - Append-only audit trail (`history: list[dict]`) tracking actor, timestamp, action, and version.
- **Cache Clearance Level Partitioning (Phần E)**:
  - Multi-tenant deterministic cache key partitioning with clearance level (`_c{clearance_level}_`).
  - Seamless caller clearance resolution from `SSOUser` and `SecurityContext` (Level 0: Public, Level 1: Internal, Level 2: Confidential, Level 3: Restricted).
- **Dead Code Cleanup & Canonical Tools (Phần F)**:
  - Removed legacy `agent_core/plugins/` directory and standalone `triage_rules.py`.
  - Standardized canonical tool registry and schema naming.

## [2.0.0] - 2026-09-01

### Added
- **Domain Pack Architecture**: Decoupled domain-specific logic into reusable declarative packs (`domain_packs/it-helpdesk/`).
- **Dynamic Agent Builder** (`agent_core/agent_builder.py`): Dynamically constructs Google GenAI ADK Agent hierarchies from `pack.yaml`, `agents.yaml`, `case_schema.yaml`, and `systems.yaml`.
- **Tool Registry** (`agent_core/tools/registry.py`): Dynamic discovery and validation of registered tools via `@register_tool` decorator.
- **Generic Case Schema** (`agent_core/tools/case_tool.py`): Domain-agnostic `CaseRecord` schema with configurable storage backend collection (`CASE_COLLECTION`).
- **Telemetry & Observability**: BigQuery `bytes_billed` metric tracking and query cancellation timeouts (`job_timeout_ms`).
- **L1 Facts & L3 Obligations Registries**: Deterministic point-lookup tools (`lookup_fact`, `get_obligation`, `list_contract_obligations`).
- **Domain Template & Guide** (`domain_packs/_template/`, `domain_packs/README.md`): Turnkey starter pack and 4-step creation guide.

### Changed
- **Package Modernization**: Renamed internal package from `it_helpdesk_agent` to `agent_core` to support multi-domain deployment.
- **Health Checks**: `/healthz` and `/readyz` endpoints now report `core_version`, active `pack_id`, and `pack_version`.
- **Documentation**: Split documentation into platform root `README.md` and domain-specific `domain_packs/it-helpdesk/README.md`.
- **Retrieval Architecture**: Over-retrieve $k=20$ candidates with RBAC post-filtering and full snippet extraction.

### Security
- **Fail-Closed Indirect Prompt Injection Defense**: Appended systematically to all agent instructions across all domain packs.
- **SSO Authentication & RBAC**: Enforced zero-trust credential evaluation with domain restrictions.

---

## [1.0.0] - 2026-08-15

### Added
- Initial enterprise IT Helpdesk autonomous agent on Google Cloud Vertex AI & BigQuery.
- Multi-tier routing architecture: Root Triage Orchestrator, L1 Self-Service, L2 Enterprise RAG, L3 Deep Diagnostics.
- BigQuery Vector Search and Firestore session persistence.
