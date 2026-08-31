# 1. Enable Required Google Cloud APIs
resource "google_project_service" "services" {
  project = var.project_id
  for_each = toset([
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "aiplatform.googleapis.com",
    "secretmanager.googleapis.com",
    "logging.googleapis.com",
    "bigquery.googleapis.com",
    "firestore.googleapis.com",
    "compute.googleapis.com"
  ])
  service            = each.key
  disable_on_destroy = false
}

# 2. Artifact Registry for Container Images
resource "google_artifact_registry_repository" "repo" {
  project       = var.project_id
  location      = var.region
  repository_id = "it-helpdesk-repo"
  description   = "Docker repository for IT Helpdesk Agent"
  format        = "DOCKER"
  depends_on    = [google_project_service.services]
}

# 3. Dedicated Service Account for Least Privilege
resource "google_service_account" "agent_sa" {
  project      = var.project_id
  account_id   = "${var.service_name}-sa"
  display_name = "IT Helpdesk Agent Service Account"
}

# 4. IAM Permissions for Vertex AI, BigQuery, Firestore, Logging, and Secret Access
resource "google_project_iam_member" "vertex_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "bigquery_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

resource "google_project_iam_member" "log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.agent_sa.email}"
}

# Scope storage admin strictly to the designated AI assets bucket (Least Privilege)
resource "google_storage_bucket_iam_member" "ai_assets_storage_user" {
  count  = var.ai_assets_bucket != "" ? 1 : 0
  bucket = var.ai_assets_bucket
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.agent_sa.email}"
}

# 5. BigQuery Dataset for Enterprise Knowledge Base (Serverless Vector Storage)
resource "google_bigquery_dataset" "kb_dataset" {
  project                    = var.project_id
  dataset_id                 = var.bigquery_kb_dataset
  friendly_name              = "IT Helpdesk Enterprise Knowledge Base"
  description                = "Dataset storing IT Helpdesk articles and vector embeddings for BigQuery VECTOR_SEARCH"
  location                   = var.region
  delete_contents_on_destroy = false
  depends_on                 = [google_project_service.services]
}

# Scope BigQuery read-only access strictly to the KB dataset (Least Privilege)
resource "google_bigquery_dataset_iam_member" "kb_dataset_viewer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.kb_dataset.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.agent_sa.email}"
}

# 6. Firestore Database for Persistent Helpdesk Ticketing
resource "google_firestore_database" "database" {
  project     = var.project_id
  name        = var.firestore_database_name
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  
  delete_protection_state = var.environment == "production" ? "DELETE_PROTECTION_ENABLED" : "DELETE_PROTECTION_DISABLED"
  deletion_policy         = var.environment == "production" ? "ABANDON" : "DELETE"

  depends_on = [google_project_service.services]
}

# 7. Secret Manager Configuration
resource "google_secret_manager_secret" "agent_secrets" {
  project   = var.project_id
  for_each  = toset(keys(var.secrets))
  secret_id = each.key
  replication {
    auto {}
  }
  depends_on = [google_project_service.services]
}

resource "google_secret_manager_secret_version" "agent_secrets_version" {
  for_each    = toset(keys(var.secrets))
  secret      = google_secret_manager_secret.agent_secrets[each.key].id
  secret_data = var.secrets[each.key]
}

resource "google_secret_manager_secret_iam_member" "secret_accessor" {
  project   = var.project_id
  for_each  = toset(keys(var.secrets))
  secret_id = google_secret_manager_secret.agent_secrets[each.key].id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.agent_sa.email}"
}

# 8. Cloud Run Service Deployment (Enterprise Production Ready)
resource "google_cloud_run_v2_service" "default" {
  project  = var.project_id
  name     = var.service_name
  location = var.region
  ingress  = var.enable_load_balancer ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_ALL"

  template {
    service_account                  = google_service_account.agent_sa.email
    timeout                          = "300s"
    max_instance_request_concurrency = var.max_instance_request_concurrency
    execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
    
    containers {
      image = "us-docker.pkg.dev/cloudrun/container/hello"

      env {
        name  = "ENVIRONMENT"
        value = var.environment
      }
      env {
        name  = "ALLOW_LOCAL_DEV_SSO"
        value = "false"
      }
      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = "global"
      }
      env {
        name  = "GOOGLE_CLOUD_REGION"
        value = var.region
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "True"
      }
      env {
        name  = "AI_ASSETS_BUCKET"
        value = var.ai_assets_bucket
      }
      env {
        name  = "SSO_CLIENT_ID"
        value = var.sso_client_id
      }
      env {
        name  = "SSO_ISSUER"
        value = var.sso_issuer
      }
      env {
        name  = "ALLOWED_DOMAINS"
        value = var.allowed_domains
      }
      env {
        name  = "KNOWLEDGE_BACKEND"
        value = var.knowledge_backend
      }
      env {
        name  = "BIGQUERY_KB_DATASET"
        value = var.bigquery_kb_dataset
      }
      env {
        name  = "USE_FIRESTORE_TICKETS"
        value = tostring(var.use_firestore_tickets)
      }
      env {
        name  = "RATE_LIMIT_ENABLED"
        value = tostring(var.rate_limit_enabled)
      }
      env {
        name  = "RATE_LIMIT_PER_MINUTE"
        value = tostring(var.rate_limit_per_minute)
      }
      env {
        name  = "AGENT_ENGINE_RESOURCE_NAME"
        value = var.agent_engine_resource_name
      }
      env {
        name  = "BEHIND_LOAD_BALANCER"
        value = tostring(var.enable_load_balancer)
      }
      env {
        name  = "SEMANTIC_CACHE_ENABLED"
        value = "true"
      }
      env {
        name  = "OTEL_TO_CLOUD"
        value = "true"
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        initial_delay_seconds = 5
        timeout_seconds       = 3
        period_seconds        = 10
        failure_threshold     = 10
      }

      liveness_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        initial_delay_seconds = 10
        timeout_seconds       = 3
        period_seconds        = 30
        failure_threshold     = 3
      }
    }

    scaling {
      min_instance_count = var.min_instance_count
      max_instance_count = var.max_instance_count
    }
  }
  
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image
    ]
  }

  depends_on = [
    google_project_service.services,
    google_firestore_database.database
  ]
}

# 9. Cloud Run Public Invoker IAM Policy (Application Auth is gated by SSO OIDC Middleware)
resource "google_cloud_run_v2_service_iam_member" "invoker" {
  count    = var.allow_unauthenticated ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.default.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

