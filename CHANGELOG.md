# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.3.0] - 2026-09-04

### Changed
- **Semantic Cache Claims & Documentation (Hạng mục A - [R1] [P1])**:
  - Corrected semantic cache performance claims in `README.md` and `Technical_Specification.md`, removing unrealistic sub-millisecond end-to-end and 100% token cost reduction assertions.
  - Documented realistic measured latencies (p50=0.003ms for InMemory, p50=0.045ms / p95=0.050ms for Redis candidate-set in-process simulation) and highlighted 1 embedding API call cost per cache query.
- **Benchmark Script Hardening & Parameterization (Hạng mục B - [R2] [P1])**:
  - Enhanced `scripts/benchmark_semantic_cache.py` with `--redis-host` and `--redis-port` support for live Memorystore testing.
  - Added explicit `IN-PROCESS SIMULATION — network cost not measured` labeling when running on `fakeredis`.
  - Parameterized LLM baseline via `--llm-baseline-ms` and integrated hit-rate evaluation (100% candidate-set accuracy vs full-scan reference).
- **Fast Loop & Secret Manager Optimization (Hạng mục C - [R1] [P2])**:
  - Added timeout guard (`timeout=2.0`) to Secret Manager remote fetch in `_fetch_secrets()` and reduced BigQuery metadata verify timeout, ensuring `pytest -m "not boot"` reliably finishes in ~12s (well under the 25s threshold).

## [2.2.0] - 2026-09-03

### Added
- **A2A Protocol & Agent Registry Integration (Work Order 5)**:
  - Mounted A2A Starlette sub-app at `/a2a` with strict inheritance of parent `SSOAuthenticationMiddleware` and `RateLimitMiddleware`, ensuring zero-trust protection on `.well-known/agent-card.json` and RPC endpoints.
  - Dynamic `AgentCard` generator reflecting domain pack metadata, multi-tier skills, and tool capabilities.
  - Cloud Run functional type flag (`functional-type = "agent"`) and Terraform declarations for Agent Registry & Agent Gateway.

### Changed
- **Production Infrastructure & Cost Clarifications**:
  - Clarified BigQuery storage and query pricing in documentation, removing obsolete idle \$0 claim.
  - Documented rationale for maintaining `min_instance_count >= 1` on Cloud Run in production: eliminates ~1.5s cold-start latency of ADK multi-tier agent initialization and BigQuery client pre-warming, guaranteeing sub-second enterprise SLA for first-token responses.
- **Security & Authorization Hardening**:
  - Enforced `require_admin` role check on `/api/cache/stats` and `/api/cache/query` to prevent unauthorized cache inspection and content enumeration.
  - Standardized InMemory knowledge store similarity scoring with `normalize_similarity()` (4 decimal places) matching BigQuery and Vertex AI Search formats.
- **CI/CD Optimization**:
  - Added `_template` domain pack test job to CI matrix on Python 3.12 (development).
  - Isolated subprocess boot tests (`-m "boot"`) into a dedicated standalone job, reducing matrix iteration time to under 25 seconds.

## [2.1.0] - 2026-09-02

### Added
- **Fail-Closed & Dynamic Pack Architecture (Phần A)**:
  - Lazy-loaded sample knowledge and obligations from active domain pack YAMLs (`knowledge.yaml`, `obligations.yaml`) avoiding memory-leak imports.
  - Fail-closed system configuration resolution (`SYSTEMS_CONFIG_PATH`) with strict validation.
  - Subprocess clean boot and AST zero-hardcode anti-regression test harness (`test_zero_hardcode_parity.py`).
- **Infrastructure & Terraform Blocking Preconditions (Phần B & C)**:
  - Added strict variable validation regex for `domain_pack` and `allowed_domains`.
  - Added Cloud Run `lifecycle { precondition }` blocks preventing insecure production deployments (`in_memory` knowledge backend, `allow_unauthenticated` without Cloud Armor, and missing `allowed_domains`).
  - Added Terraform `required_version = ">= 1.9"` and documented advisory SLA warning check for preview models.
  - Hardened Redis Memorystore with `auth_enabled = true`, in-transit TLS encryption, Secret Manager secret storage for auth string and CA certificate, and secure Cloud Run container injection via `value_source.secret_key_ref`.
  - Enforced client-side TLS CA certificate verification (`ssl_cert_reqs="required"`) and fail-closed authentication in production mode.
- **Case Lifecycle State Machine & RBAC (Phần C)**:
  - Enforced deterministic status transition whitelist (`Open -> In_Progress -> Resolved -> Closed`) and terminal state immutability.
  - Enforced Role-Based Access Control (RBAC) on `route_case_to_tier` (rejecting unprivileged escalation to L2/L3).
- **Firestore Fail-Closed, OCC & Audit History (Phần D)**:
  - Enforced fail-closed persistence in production mode (`RuntimeError` on connection loss, no silent in-memory fallback).
  - Optimistic Concurrency Control (OCC) with incremental version counters and 409 conflict detection.
  - Append-only audit trail (`history: list[dict]`) tracking actor, timestamp, action, and version.
- **Cache Clearance Level Partitioning & Public Protection (Phần E)**:
  - Multi-tenant deterministic cache key partitioning with clearance level (`_c{clearance_level}_`).
  - Seamless caller clearance resolution from `SSOUser` and `SecurityContext` (Level 0: Public, Level 1: Internal, Level 2: Confidential, Level 3: Restricted).
  - Enforced strict protection blocking `is_public=True` when `clearance_level > 0`, preventing sensitive or confidential items from being stored as public FAQ cache.
- **Dead Code Cleanup, Tool Canonicalization & MCP Deprecation (Phần F & G)**:
  - Removed legacy `agent_core/plugins/` directory and standalone `triage_rules.py`.
  - Standardized canonical tool registry and schema naming in `agent_core.tools.registry`.
  - Marked `agent_core.tools.mcp_config` as deprecated in favor of declarative domain pack tool resolution.

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
