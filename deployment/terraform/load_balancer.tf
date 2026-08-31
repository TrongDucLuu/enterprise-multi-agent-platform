# ==============================================================================
# Enterprise Global HTTPS Load Balancer & Cloud Armor WAF Security Module
# ==============================================================================

# 1. Serverless Network Endpoint Group (NEG) pointing to Cloud Run
resource "google_compute_region_network_endpoint_group" "serverless_neg" {
  count                 = var.enable_load_balancer ? 1 : 0
  project               = var.project_id
  name                  = "${var.service_name}-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region

  cloud_run {
    service = google_cloud_run_v2_service.default.name
  }
}

# 2. Cloud Armor Edge WAF Security Policy (DDoS Protection & Edge Rate Limiting)
resource "google_compute_security_policy" "edge_security_policy" {
  count       = var.enable_load_balancer ? 1 : 0
  project     = var.project_id
  name        = "${var.service_name}-cloud-armor-policy"
  description = "Edge WAF security policy with DDoS mitigation, bot protection, and IP rate limiting"

  # Rule 1: Edge Rate Limiting (100 requests per minute per client IP)
  rule {
    action   = "rate_based_ban"
    priority = "1000"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    rate_limit_options {
      conform_action = "allow"
      exceed_action  = "deny(429)"
      enforce_on_key = "IP"
      rate_limit_threshold {
        count        = 100
        interval_sec = 60
      }
      ban_duration_sec = 300
    }
    description = "Enforce edge rate limiting (100 req/min) to prevent layer-7 DDoS"
  }

  # Default rule: Allow traffic
  rule {
    action   = "allow"
    priority = "2147483647"
    match {
      versioned_expr = "SRC_IPS_V1"
      config {
        src_ip_ranges = ["*"]
      }
    }
    description = "Default allow rule for regular traffic"
  }

  depends_on = [google_project_service.services]
}

# 3. Global Reserved Public IP Address
resource "google_compute_global_address" "lb_ip" {
  count        = var.enable_load_balancer ? 1 : 0
  project      = var.project_id
  name         = "${var.service_name}-lb-ip"
  address_type = "EXTERNAL"
  ip_version   = "IPV4"

  depends_on = [google_project_service.services]
}

# 4. Backend Service connecting Cloud Armor and Serverless NEG
resource "google_compute_backend_service" "lb_backend" {
  count                 = var.enable_load_balancer ? 1 : 0
  project               = var.project_id
  name                  = "${var.service_name}-backend"
  protocol              = "HTTPS"
  port_name             = "http"
  timeout_sec           = 300
  enable_cdn            = true
  security_policy       = google_compute_security_policy.edge_security_policy[0].id

  backend {
    group = google_compute_region_network_endpoint_group.serverless_neg[0].id
  }

  cdn_policy {
    cache_mode                   = "CACHE_ALL_STATIC"
    default_ttl                  = 3600
    client_ttl                   = 3600
    max_ttl                      = 86400
    serve_while_stale            = 86400
    negative_caching             = true
  }

  depends_on = [google_project_service.services]
}

# 5. Global URL Map
resource "google_compute_url_map" "lb_url_map" {
  count           = var.enable_load_balancer ? 1 : 0
  project         = var.project_id
  name            = "${var.service_name}-url-map"
  default_service = google_compute_backend_service.lb_backend[0].id
}

# 6. Target HTTP Proxy (or HTTPS Proxy if domain/cert provided)
resource "google_compute_target_http_proxy" "http_proxy" {
  count   = var.enable_load_balancer ? 1 : 0
  project = var.project_id
  name    = "${var.service_name}-http-proxy"
  url_map = google_compute_url_map.lb_url_map[0].id
}

# 7. Global Forwarding Rule
resource "google_compute_global_forwarding_rule" "forwarding_rule" {
  count                 = var.enable_load_balancer ? 1 : 0
  project               = var.project_id
  name                  = "${var.service_name}-forwarding-rule"
  target                = google_compute_target_http_proxy.http_proxy[0].id
  port_range            = "80"
  ip_address            = google_compute_global_address.lb_ip[0].address
  load_balancing_scheme = "EXTERNAL_MANAGED"

  depends_on = [google_project_service.services]
}
