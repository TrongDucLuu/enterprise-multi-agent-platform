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

variable "secrets" {
  description = "A map of secret names and their values (e.g., HELPDESK_ADMIN_API_KEY)"
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
  description = "The SSO / OIDC Client ID for enterprise authentication"
  type        = string
  default     = "it-helpdesk-agent-client-id"
}

variable "sso_issuer" {
  description = "The SSO / OIDC Issuer URL (e.g., https://accounts.google.com)"
  type        = string
  default     = "https://accounts.google.com"
}

