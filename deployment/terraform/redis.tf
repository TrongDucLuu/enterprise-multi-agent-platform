# ==============================================================================
# Enterprise VPC Network & Memorystore for Redis Module
# Provides shared cluster state for Rate Limiting & Semantic Cache
# ==============================================================================

# 1. Dedicated VPC Network for IT Helpdesk Infrastructure
resource "google_compute_network" "app_vpc" {
  project                 = var.project_id
  name                    = "${var.service_name}-vpc"
  auto_create_subnetworks = false
  description             = "VPC network for IT Helpdesk Cloud Run Direct VPC Egress and Memorystore Redis"
  depends_on              = [google_project_service.services]
}

# 2. Regional Subnetwork for Cloud Run Direct VPC Egress
resource "google_compute_subnetwork" "app_subnet" {
  project                  = var.project_id
  name                     = "${var.service_name}-subnet"
  ip_cidr_range            = "10.10.0.0/24"
  region                   = var.region
  network                  = google_compute_network.app_vpc.id
  private_ip_google_access = true
  description              = "Subnet for Cloud Run Gen2 Direct VPC Egress"
}

# 3. Private IP Range Allocation for Google Services Peering (Memorystore)
resource "google_compute_global_address" "private_ip_alloc" {
  count         = var.redis_enabled ? 1 : 0
  project       = var.project_id
  name          = "${var.service_name}-redis-ip-range"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = 20
  network       = google_compute_network.app_vpc.id
  depends_on    = [google_project_service.services]
}

# 4. Service Networking Connection for Private Services Access
resource "google_service_networking_connection" "private_vpc_connection" {
  count                   = var.redis_enabled ? 1 : 0
  network                 = google_compute_network.app_vpc.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_ip_alloc[0].name]
  depends_on              = [google_project_service.services]
}

# 5. Memorystore for Redis Instance (Shared Rate Limiter & Semantic Cache)
resource "google_redis_instance" "cache_redis" {
  count              = var.redis_enabled ? 1 : 0
  project            = var.project_id
  name               = "${var.service_name}-redis"
  tier               = var.environment == "production" ? "STANDARD_HA" : "BASIC"
  memory_size_gb     = var.redis_memory_size_gb
  region             = var.region
  authorized_network      = google_compute_network.app_vpc.id
  connect_mode            = "PRIVATE_SERVICE_ACCESS"
  redis_version           = "REDIS_7_0"
  display_name            = "IT Helpdesk Shared Redis Cache"
  auth_enabled            = true
  transit_encryption_mode = "SERVER_AUTHENTICATION"

  labels = {
    environment = var.environment
    managed_by  = "terraform"
    service     = var.service_name
  }

  maintenance_policy {
    weekly_maintenance_window {
      day = "SUNDAY"
      start_time {
        hours   = 2
        minutes = 0
        seconds = 0
        nanos   = 0
      }
    }
  }

  depends_on = [
    google_service_networking_connection.private_vpc_connection,
    google_project_service.services
  ]
}

# 6. Secret Manager Secrets for Redis Auth String and Server CA Certificate
resource "google_secret_manager_secret" "redis_auth" {
  count     = var.redis_enabled ? 1 : 0
  project   = var.project_id
  secret_id = "${var.service_name}-redis-auth"
  replication {
    auto {}
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "redis_auth_version" {
  count       = var.redis_enabled ? 1 : 0
  secret      = google_secret_manager_secret.redis_auth[0].id
  secret_data = google_redis_instance.cache_redis[0].auth_string
}

resource "google_secret_manager_secret_iam_member" "redis_auth_accessor" {
  count     = var.redis_enabled ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.redis_auth[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_secret_manager_secret" "redis_ca_cert" {
  count     = var.redis_enabled ? 1 : 0
  project   = var.project_id
  secret_id = "${var.service_name}-redis-ca-cert"
  replication {
    auto {}
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "redis_ca_cert_version" {
  count       = var.redis_enabled ? 1 : 0
  secret      = google_secret_manager_secret.redis_ca_cert[0].id
  secret_data = google_redis_instance.cache_redis[0].server_ca_certs[0].cert
}

resource "google_secret_manager_secret_iam_member" "redis_ca_cert_accessor" {
  count     = var.redis_enabled ? 1 : 0
  project   = var.project_id
  secret_id = google_secret_manager_secret.redis_ca_cert[0].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent_sa.email}"
}
