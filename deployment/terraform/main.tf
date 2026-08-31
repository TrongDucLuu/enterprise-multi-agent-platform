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
    "compute.googleapis.com",
    "redis.googleapis.com",
    "servicenetworking.googleapis.com"
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

# 5. BigQuery Dataset & Vector Table for Enterprise Knowledge Base
resource "google_bigquery_dataset" "kb_dataset" {
  project                    = var.project_id
  dataset_id                 = var.bigquery_kb_dataset
  friendly_name              = "IT Helpdesk Enterprise Knowledge Base"
  description                = "Dataset storing IT Helpdesk articles and vector embeddings for BigQuery VECTOR_SEARCH"
  location                   = var.region
  delete_contents_on_destroy = false
  depends_on                 = [google_project_service.services]
}

resource "google_bigquery_table" "knowledge_articles" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.kb_dataset.dataset_id
  table_id            = "knowledge_articles"
  friendly_name       = "Enterprise Knowledge Articles"
  description         = "Knowledge base articles with 768-dimensional text-embedding-005 vectors for enterprise semantic search"
  deletion_protection = var.environment == "production" ? true : false

  clustering = ["system", "category"]

  schema = <<EOF
[
  {
    "name": "id",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Unique article identifier (e.g. ERP-KB-001)"
  },
  {
    "name": "system",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Enterprise system identifier (e.g. ERP, HRM, CRM)"
  },
  {
    "name": "title",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Article title"
  },
  {
    "name": "category",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Category or topic (e.g. Finance & Procurement)"
  },
  {
    "name": "content",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Full text or markdown content of the guide/troubleshooting procedure"
  },
  {
    "name": "keywords",
    "type": "STRING",
    "mode": "REPEATED",
    "description": "Search keywords and acronyms"
  },
  {
    "name": "embedding",
    "type": "FLOAT64",
    "mode": "REPEATED",
    "description": "Dense vector embedding (768 dimensions, text-embedding-005)"
  },
  {
    "name": "source_uri",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Source document location (e.g. gs://bucket/docs/manual.docx)"
  },
  {
    "name": "owner",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Content owner or author department"
  },
  {
    "name": "effective_date",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Effective date for document validity in ISO-8601 YYYY-MM-DD"
  },
  {
    "name": "expiry_date",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Expiration date for document validity in ISO-8601 YYYY-MM-DD"
  },
  {
    "name": "is_deleted",
    "type": "BOOLEAN",
    "mode": "NULLABLE",
    "description": "Tombstone flag indicating if document is soft-deleted"
  },
  {
    "name": "deleted_at",
    "type": "TIMESTAMP",
    "mode": "NULLABLE",
    "description": "Timestamp when the document was marked as tombstoned"
  },
  {
    "name": "parser_version",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Version of document parser used (e.g. 1.0.0)"
  },
  {
    "name": "chunker_version",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Version of chunking algorithm used (e.g. 1.0.0)"
  },
  {
    "name": "embedding_model",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Model used for embedding generation (e.g. text-embedding-005)"
  },
  {
    "name": "embedding_dim",
    "type": "INTEGER",
    "mode": "NULLABLE",
    "description": "Dimension of embedding vector (e.g. 768)"
  },
  {
    "name": "content_hash",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "SHA-256 hash of raw content for CDC change detection"
  },
  {
    "name": "section_hierarchy",
    "type": "RECORD",
    "mode": "NULLABLE",
    "description": "Hierarchical document outline (h1, h2, h3)",
    "fields": [
      {
        "name": "h1",
        "type": "STRING",
        "mode": "NULLABLE",
        "description": "Top-level heading (H1)"
      },
      {
        "name": "h2",
        "type": "STRING",
        "mode": "NULLABLE",
        "description": "Sub-heading (H2)"
      },
      {
        "name": "h3",
        "type": "STRING",
        "mode": "NULLABLE",
        "description": "Sub-sub-heading (H3)"
      }
    ]
  },
  {
    "name": "updated_at",
    "type": "TIMESTAMP",
    "mode": "REQUIRED",
    "description": "Timestamp when this article was created or updated"
  }
]
EOF

  depends_on = [google_bigquery_dataset.kb_dataset]
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
      env {
        name  = "USE_VERTEX_EMBEDDING"
        value = tostring(var.use_vertex_embedding)
      }
      env {
        name  = "SYSTEMS_CONFIG_PATH"
        value = var.systems_config_path
      }
      env {
        name  = "FAST_MODEL_NAME"
        value = var.fast_model_name
      }
      env {
        name  = "REASONING_MODEL_NAME"
        value = var.reasoning_model_name
      }
      env {
        name  = "TELEMETRY_ANONYMIZE_USERS"
        value = tostring(var.telemetry_anonymize_users)
      }
      env {
        name  = "TELEMETRY_INCLUDE_QUERY"
        value = tostring(var.telemetry_include_query)
      }
      env {
        name  = "RATE_LIMIT_BACKEND"
        value = var.redis_enabled ? "redis" : "memory"
      }
      env {
        name  = "SEMANTIC_CACHE_BACKEND"
        value = var.redis_enabled ? "redis" : "memory"
      }
      env {
        name  = "REDIS_HOST"
        value = var.redis_enabled ? google_redis_instance.cache_redis[0].host : ""
      }
      env {
        name  = "REDIS_PORT"
        value = var.redis_enabled ? tostring(google_redis_instance.cache_redis[0].port) : "6379"
      }
      env {
        name  = "L3_RATE_LIMIT_PER_MINUTE"
        value = tostring(var.l3_rate_limit_per_minute)
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

    dynamic "vpc_access" {
      for_each = var.redis_enabled ? [1] : []
      content {
        network_interfaces {
          network    = google_compute_network.app_vpc.id
          subnetwork = google_compute_subnetwork.app_subnet.id
        }
        egress = "PRIVATE_RANGES_ONLY"
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
    google_firestore_database.database,
    google_redis_instance.cache_redis
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

# 10. Enterprise Production Guardrails & SLA Checks
check "production_edge_security" {
  assert {
    condition = !(var.environment == "production" && var.allow_unauthenticated && !var.enable_cloud_armor)
    error_message = "CRITICAL SECURITY WARNING: In 'production' environment, setting allow_unauthenticated=true exposes Cloud Run directly without WAF / DDoS protection. Enable Google Cloud Armor (enable_cloud_armor=true) or set allow_unauthenticated=false."
  }
}

check "production_model_sla" {
  assert {
    condition = !(var.environment == "production" && (can(regex("preview", var.fast_model_name)) || can(regex("preview", var.reasoning_model_name))))
    error_message = "PRODUCTION SLA WARNING: Preview models (e.g. gemini-3-flash-preview) do not have Google Cloud Vertex AI 99.9% uptime enterprise SLA commitments. For production deployments, ensure GA models (gemini-2.5-flash and gemini-2.5-pro) are selected."
  }
}


