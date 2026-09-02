variable "project_id" {
  description = "The Google Cloud Project ID"
  type        = string
}

variable "region" {
  description = "The Google Cloud region to deploy to"
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "The name of the Cloud Run service"
  type        = string
  default     = "enterprise-multi-agent-platform"
}

variable "environment" {
  description = "Deployment environment (production, staging, development)"
  type        = string
  default     = "development"
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "environment must be one of: 'development', 'staging', 'production'."
  }
}

variable "domain_pack" {
  description = "Active domain pack name located in domain_packs/ directory (e.g., 'it-helpdesk', '_template')"
  type        = string
  default     = "it-helpdesk"
  validation {
    condition     = can(regex("^[a-zA-Z0-9_-]+$", var.domain_pack)) && length(var.domain_pack) > 0
    error_message = "domain_pack must be a non-empty alphanumeric string (hyphens and underscores allowed)."
  }
}

variable "secrets" {
  description = "A map of secret names and their values (e.g., HELPDESK_ADMIN_API_KEY, SSO_CLIENT_SECRET, SSO_JWT_SECRET)"
  type        = map(string)
  sensitive   = true
  default     = {}
}

variable "ai_assets_bucket" {
  description = "The GCS bucket for ADK session / artifact service uploads and storage (requires read/write permissions for ADK)"
  type        = string
  default     = ""
}

variable "allowed_artifact_bucket" {
  description = "The GCS bucket containing enterprise contract/compliance/log artifacts for L3 inspection tools (strictly read-only objectViewer access)"
  type        = string
  default     = ""
}

variable "sso_client_id" {
  description = "The SSO / OIDC Client ID for enterprise authentication (Google Workspace OAuth Client ID)"
  type        = string
}

variable "sso_issuer" {
  description = "The SSO / OIDC Issuer URL (e.g., https://accounts.google.com)"
  type        = string
  default     = "https://accounts.google.com"
}

variable "allowed_domains" {
  description = "Comma-separated list of enterprise domains permitted to access (e.g. 'company.com,subsidiary.com')"
  type        = string
  validation {
    condition     = length(trimspace(var.allowed_domains)) > 0 && !can(regex("(^|[,\\s])\\*([,\\s]|$)", var.allowed_domains))
    error_message = "allowed_domains must be non-empty and cannot contain wildcard '*' domain."
  }
}

variable "enable_cloud_identity_group_lookup" {
  description = "Enable Google Cloud Identity / Workspace group membership lookups for SSO RBAC"
  type        = bool
  default     = false
}

variable "sso_groups_claim" {
  description = "JWT claim name for extracting enterprise security groups from SSO tokens"
  type        = string
  default     = "groups"
}

variable "knowledge_backend" {
  description = "Knowledge base backend ('in_memory', 'bigquery', 'vertex_ai_search')"
  type        = string
  default     = "bigquery"
  validation {
    condition     = contains(["in_memory", "bigquery", "vertex_ai_search"], var.knowledge_backend)
    error_message = "knowledge_backend must be one of: 'in_memory', 'bigquery', 'vertex_ai_search'."
  }
}

variable "vertex_search_data_store_id" {
  description = "Vertex AI Search Datastore ID when using 'vertex_ai_search' backend"
  type        = string
  default     = "enterprise-knowledge-store"
}

variable "vertex_search_location" {
  description = "Vertex AI Search Datastore location (e.g. 'global', 'asia-southeast1')"
  type        = string
  default     = "global"
}

variable "vertex_search_collection_id" {
  description = "Vertex AI Search Collection ID"
  type        = string
  default     = "default_collection"
}

variable "bigquery_kb_dataset" {
  description = "BigQuery dataset ID for storing knowledge articles and vector embeddings"
  type        = string
  default     = "it_helpdesk_kb"
}

variable "max_instance_count" {
  description = "Maximum number of Cloud Run container instances. Enterprise Sizing Formula: ceil(Peak_CCU / max_instance_request_concurrency) * 1.5"
  type        = number
  default     = 10
}

variable "min_instance_count" {
  description = "Minimum number of Cloud Run container instances (0 allows scaling to zero to minimize idle costs; >= 1 in production to eliminate cold starts)"
  type        = number
  default     = 0
}

variable "max_instance_request_concurrency" {
  description = "Maximum concurrent requests per container instance (recommended 4-8 for LLM reasoning workloads)"
  type        = number
  default     = 8
}

variable "redis_enabled" {
  description = "Provision Google Cloud Memorystore for Redis for cluster-wide rate limiting and semantic caching"
  type        = bool
  default     = true
}

variable "redis_memory_size_gb" {
  description = "Memory capacity in GiB for Memorystore Redis instance (1 GiB supports ~50,000 active users + cache entries)"
  type        = number
  default     = 1
}

variable "l3_rate_limit_per_minute" {
  description = "Rate limit threshold for expensive L3 Gemini Pro reasoning calls per user per minute"
  type        = number
  default     = 10
}

variable "allow_unauthenticated" {
  description = "Whether to allow unauthenticated public HTTP access at Cloud Run layer (SSO auth is enforced at application middleware)"
  type        = bool
  default     = false
}

variable "use_firestore_tickets" {
  description = "Enable Google Cloud Firestore for persistent helpdesk ticket storage"
  type        = bool
  default     = true
}

variable "rate_limit_enabled" {
  description = "Enable sliding window rate limiting"
  type        = bool
  default     = true
}

variable "rate_limit_per_minute" {
  description = "Rate limit threshold per client per minute"
  type        = number
  default     = 60
}

variable "agent_engine_resource_name" {
  description = "Pre-provisioned Vertex AI Agent Engine resource name (optional, overrides auto-creation at runtime)"
  type        = string
  default     = ""
}

variable "firestore_database_name" {
  description = "Name of the Firestore database instance ('(default)' or custom named database)"
  type        = string
  default     = "(default)"
}

variable "enable_load_balancer" {
  description = "Provision a Google Cloud Global HTTPS Load Balancer with Cloud Armor WAF and SSL (Recommended for Enterprise Production)"
  type        = bool
  default     = false
}

variable "domain_name" {
  description = "Custom domain name for Google-managed SSL Certificate (e.g., helpdesk.corp.example.com)"
  type        = string
  default     = ""
}

variable "use_vertex_embedding" {
  description = "Enable Vertex AI text-embedding-005 for generating dense vector embeddings"
  type        = bool
  default     = true
}

variable "systems_config_path" {
  description = "Path to the systems and RBAC configuration YAML file"
  type        = string
  default     = "/code/config/systems.yaml"
}

variable "enable_cloud_armor" {
  description = "Enable Google Cloud Armor WAF and HTTPS Load Balancer in front of Cloud Run for DDoS and OWASP protection"
  type        = bool
  default     = false
}

variable "fast_model_name" {
  description = "Gemini model name for standard triage, L1 and L2 agents. GA default: 'gemini-2.5-flash' (guaranteed 99.9% Vertex AI SLA). For experimental preview, override with 'gemini-3-flash-preview'."
  type        = string
  default     = "gemini-2.5-flash"
}

variable "reasoning_model_name" {
  description = "Gemini model name for L3 reasoning diagnostics & compliance. GA default: 'gemini-2.5-pro' (guaranteed 99.9% Vertex AI SLA). For experimental preview, override with 'gemini-3-pro-preview'."
  type        = string
  default     = "gemini-2.5-pro"
}

variable "telemetry_anonymize_users" {
  description = "Hash user IDs (SHA-256) in telemetry logs for GDPR/HIPAA/Banking compliance (Fail-closed default: true)"
  type        = bool
  default     = true
}

variable "telemetry_include_query" {
  description = "Include query snippets in telemetry logs (Fail-closed default: false for sensitive data protection)"
  type        = bool
  default     = false
}

variable "enable_bigquery_reservation" {
  description = "Enable BigQuery Edition Reservation with slot autoscaling for low-latency vector search"
  type        = bool
  default     = false
}

variable "bigquery_edition" {
  description = "BigQuery Edition for capacity reservation ('ENTERPRISE' or 'ENTERPRISE_PLUS')"
  type        = string
  default     = "ENTERPRISE"
}

variable "bigquery_baseline_slots" {
  description = "Baseline dedicated slots for BigQuery Reservation (0 for pure autoscaling)"
  type        = number
  default     = 0
}

variable "bigquery_max_slots" {
  description = "Maximum autoscaling slots for BigQuery Reservation"
  type        = number
  default     = 100
}



