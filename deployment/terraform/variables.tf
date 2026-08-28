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
  default     = "it-helpdesk-agent"
}

variable "environment" {
  description = "Deployment environment (production, staging, development)"
  type        = string
  default     = "production"
}

variable "secrets" {
  description = "A map of secret names and their values (e.g., HELPDESK_ADMIN_API_KEY, SSO_CLIENT_SECRET, SSO_JWT_SECRET)"
  type        = map(string)
  sensitive   = true
  default     = {}
}

variable "ai_assets_bucket" {
  description = "The GCS bucket for storing IT Helpdesk assets and artifacts"
  type        = string
  default     = ""
}

variable "sso_client_id" {
  description = "The SSO / OIDC Client ID for enterprise authentication (Google Workspace OAuth Client ID)"
  type        = string
  default     = "it-helpdesk-agent-client-id"
}

variable "sso_issuer" {
  description = "The SSO / OIDC Issuer URL (e.g., https://accounts.google.com)"
  type        = string
  default     = "https://accounts.google.com"
}

variable "allowed_domains" {
  description = "Comma-separated list of enterprise domains permitted to access (e.g. 'company.com,subsidiary.com')"
  type        = string
  default     = ""
}

variable "knowledge_backend" {
  description = "Knowledge base backend ('in_memory', 'bigquery')"
  type        = string
  default     = "in_memory"
}

variable "bigquery_kb_dataset" {
  description = "BigQuery dataset ID for storing knowledge articles and vector embeddings"
  type        = string
  default     = "it_helpdesk_kb"
}
