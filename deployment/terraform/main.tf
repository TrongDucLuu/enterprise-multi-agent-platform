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
    "servicenetworking.googleapis.com",
    "cloudidentity.googleapis.com"
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

# Storage IAM:
# 1. ADK AI Assets / Artifact Service / Sessions (Requires read/write/delete permissions -> roles/storage.objectUser)
resource "google_storage_bucket_iam_member" "ai_assets_storage_user" {
  count  = var.ai_assets_bucket != "" ? 1 : 0
  bucket = var.ai_assets_bucket
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${google_service_account.agent_sa.email}"
}

# 2. Enterprise Allowed Artifacts (Contracts, compliance evidence, inspection logs -> strictly roles/storage.objectViewer)
resource "google_storage_bucket_iam_member" "allowed_artifacts_viewer" {
  count  = var.allowed_artifact_bucket != "" ? 1 : 0
  bucket = var.allowed_artifact_bucket
  role   = "roles/storage.objectViewer"
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
  description         = "Knowledge base articles with 768-dimensional text-multilingual-embedding-002 vectors for enterprise semantic search"
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
    "name": "parent_doc_id",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Parent document identifier across chunk boundaries"
  },
  {
    "name": "chunk_index",
    "type": "INTEGER",
    "mode": "NULLABLE",
    "description": "Zero-based index of the chunk within the document"
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
    "name": "section_h1",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Top-level heading (H1)"
  },
  {
    "name": "section_h2",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Sub-heading (H2)"
  },
  {
    "name": "section_h3",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Sub-sub-heading (H3)"
  },
  {
    "name": "allowed_roles",
    "type": "STRING",
    "mode": "REPEATED",
    "description": "Authorized SSO roles allowed to retrieve this article"
  },
  {
    "name": "sensitivity",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Data sensitivity classification level (e.g. PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED)"
  },
  {
    "name": "clearance_level",
    "type": "INTEGER",
    "mode": "NULLABLE",
    "description": "Numeric clearance level: 0=PUBLIC, 1=INTERNAL, 2=CONFIDENTIAL, 3=RESTRICTED"
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
    "type": "DATE",
    "mode": "NULLABLE",
    "description": "Effective date for document validity in ISO-8601 YYYY-MM-DD"
  },
  {
    "name": "expiry_date",
    "type": "DATE",
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
    "name": "updated_at",
    "type": "TIMESTAMP",
    "mode": "REQUIRED",
    "description": "Timestamp when this article was created or updated"
  }
]
EOF

  depends_on = [google_bigquery_dataset.kb_dataset]
}

resource "google_bigquery_table" "ingestion_dead_letter_queue" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.kb_dataset.dataset_id
  table_id            = "ingestion_dead_letter_queue"
  friendly_name       = "Knowledge Ingestion Dead Letter Queue"
  description         = "Persistent DLQ table storing unparseable or failed documents with error tracebacks"
  deletion_protection = var.environment == "production" ? true : false

  clustering = ["stage"]

  schema = <<EOF
[
  {
    "name": "id",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Unique DLQ entry UUID"
  },
  {
    "name": "file_path",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Source file path of the unparseable/failed document"
  },
  {
    "name": "stage",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Ingestion pipeline stage where failure occurred (parsing, chunking, embedding, loading)"
  },
  {
    "name": "error_message",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Detailed error message and traceback snippet"
  },
  {
    "name": "doc_title",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Extracted document title if available"
  },
  {
    "name": "doc_payload",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Serialized raw JSON payload of the failing document"
  },
  {
    "name": "occurred_at",
    "type": "TIMESTAMP",
    "mode": "REQUIRED",
    "description": "UTC timestamp when the ingestion error occurred"
  }
]
EOF

  depends_on = [google_bigquery_dataset.kb_dataset]
}

resource "google_bigquery_table" "l1_facts" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.kb_dataset.dataset_id
  table_id            = "l1_facts"
  friendly_name       = "Enterprise L1 Facts Store"
  description         = "Deterministic enterprise facts with clearance levels and RBAC role restrictions"
  deletion_protection = var.environment == "production" ? true : false

  clustering = ["domain", "key"]

  schema = <<EOF
[
  {
    "name": "fact_id",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Unique fact identifier (e.g. FACT-ERP-001)"
  },
  {
    "name": "domain",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Domain or system name (e.g. ERP, HRM, CRM)"
  },
  {
    "name": "key",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Deterministic fact key (e.g. erp.po.sla_hours)"
  },
  {
    "name": "value",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Fact string or serialized value"
  },
  {
    "name": "value_type",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Data type of value (int, float, string, bool)"
  },
  {
    "name": "unit",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Measurement unit if applicable"
  },
  {
    "name": "source_document",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Source reference document path"
  },
  {
    "name": "date_updated",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Last updated date in YYYY-MM-DD"
  },
  {
    "name": "updated_by",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Author or pipeline that updated this fact"
  },
  {
    "name": "status",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Lifecycle status (active, deprecated, superseded)"
  },
  {
    "name": "notes",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Operational notes or business context"
  },
  {
    "name": "clearance_level",
    "type": "INTEGER",
    "mode": "NULLABLE",
    "description": "Numeric clearance level required (0=PUBLIC, 1=INTERNAL, 2=CONFIDENTIAL, 3=RESTRICTED)"
  },
  {
    "name": "allowed_roles",
    "type": "STRING",
    "mode": "REPEATED",
    "description": "Authorized SSO roles allowed to query this fact"
  }
]
EOF

  depends_on = [google_bigquery_dataset.kb_dataset]
}

resource "google_bigquery_table" "l3_obligations" {
  project             = var.project_id
  dataset_id          = google_bigquery_dataset.kb_dataset.dataset_id
  table_id            = "l3_obligations"
  friendly_name       = "Enterprise L3 Contract Obligations"
  description         = "SLA and legal contract obligations with RBAC and MAC controls"
  deletion_protection = var.environment == "production" ? true : false

  clustering = ["source_id", "severity"]

  schema = <<EOF
[
  {
    "name": "obligation_id",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Unique obligation identifier (e.g. OBL-SAP-001)"
  },
  {
    "name": "source_id",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Source contract ID"
  },
  {
    "name": "source_title",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Source contract title"
  },
  {
    "name": "authority",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Governing authority or signing party"
  },
  {
    "name": "article",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Contract section or clause number"
  },
  {
    "name": "description",
    "type": "STRING",
    "mode": "REQUIRED",
    "description": "Obligation description or legal requirement"
  },
  {
    "name": "severity",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Severity level (critical, high, medium, low)"
  },
  {
    "name": "applies_to",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Target party (vendor, customer, both)"
  },
  {
    "name": "date_added",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Date obligation added in YYYY-MM-DD"
  },
  {
    "name": "date_effective",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Effective start date in YYYY-MM-DD"
  },
  {
    "name": "date_expires",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Expiration date in YYYY-MM-DD"
  },
  {
    "name": "status",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Lifecycle status (active, superseded, expired)"
  },
  {
    "name": "source_document_path",
    "type": "STRING",
    "mode": "NULLABLE",
    "description": "Cloud Storage path to full signed contract PDF"
  },
  {
    "name": "clearance_level",
    "type": "INTEGER",
    "mode": "NULLABLE",
    "description": "Numeric clearance level required (0=PUBLIC, 1=INTERNAL, 2=CONFIDENTIAL, 3=RESTRICTED)"
  },
  {
    "name": "allowed_roles",
    "type": "STRING",
    "mode": "REPEATED",
    "description": "Authorized SSO roles allowed to inspect this obligation"
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

# BigQuery Enterprise Edition Reservation for Vector Search Auto-scaling (Fluid Scaling)
resource "google_bigquery_reservation" "enterprise_rag_reservation" {
  count             = var.enable_bigquery_reservation ? 1 : 0
  name              = "it-helpdesk-rag-reservation"
  project           = var.project_id
  location          = var.region
  slot_capacity     = var.bigquery_baseline_slots
  edition           = var.bigquery_edition
  ignore_idle_slots = false

  autoscale {
    max_slots = var.bigquery_max_slots
  }
}

resource "google_bigquery_reservation_assignment" "enterprise_rag_assignment" {
  count       = var.enable_bigquery_reservation ? 1 : 0
  project     = var.project_id
  location    = var.region
  reservation = google_bigquery_reservation.enterprise_rag_reservation[0].id
  job_type    = "QUERY"
  assignee    = "projects/${var.project_id}"
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
        name  = "DOMAIN_PACK"
        value = var.domain_pack
      }
      env {
        name  = "AI_ASSETS_BUCKET"
        value = var.ai_assets_bucket
      }
      env {
        name  = "ALLOWED_ARTIFACT_BUCKET"
        value = var.allowed_artifact_bucket
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
        name  = "ENABLE_CLOUD_IDENTITY_GROUP_LOOKUP"
        value = tostring(var.enable_cloud_identity_group_lookup)
      }
      env {
        name  = "SSO_GROUPS_CLAIM"
        value = var.sso_groups_claim
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
        name  = "VERTEX_SEARCH_DATA_STORE_ID"
        value = var.vertex_search_data_store_id
      }
      env {
        name  = "VERTEX_SEARCH_LOCATION"
        value = var.vertex_search_location
      }
      env {
        name  = "VERTEX_SEARCH_COLLECTION_ID"
        value = var.vertex_search_collection_id
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
        name  = "REDIS_AUTH_STRING"
        value = var.redis_enabled ? google_redis_instance.cache_redis[0].auth_string : ""
      }
      env {
        name  = "REDIS_USE_TLS"
        value = var.redis_enabled ? "true" : "false"
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
          path = "/readyz"
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
    precondition {
      condition     = var.environment != "production" || (!var.allow_local_dev_sso && length(trimspace(var.allowed_domains)) > 0 && !can(regex("(^|[,\\s])\\*([,\\s]|$)", var.allowed_domains)))
      error_message = "Production deployment requires explicit non-wildcard allowed_domains and allow_local_dev_sso=false."
    }
    precondition {
      condition     = var.environment != "production" || (var.min_instance_count >= 1 && var.max_instance_count >= 2)
      error_message = "Production deployment requires min_instance_count >= 1 and max_instance_count >= 2 for high availability."
    }
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

check "production_knowledge_backend" {
  assert {
    condition = !(var.environment == "production" && var.knowledge_backend == "in_memory")
    error_message = "CRITICAL CONFIGURATION ERROR: Production environment cannot use 'in_memory' knowledge backend. In-memory knowledge base loses all vector search, dynamic updates, and document governance capabilities. Set knowledge_backend = 'bigquery'."
  }
}

check "production_allowed_domains" {
  assert {
    condition = !(var.environment == "production" && trimspace(var.allowed_domains) == "")
    error_message = "CRITICAL SECURITY CONFIGURATION ERROR: Production environment requires non-empty 'allowed_domains' to enforce SSO email domain shielding. Set allowed_domains (e.g. 'company.com,subsidiary.com')."
  }
}


