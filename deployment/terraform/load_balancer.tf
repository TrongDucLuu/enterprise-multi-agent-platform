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

# 2. Cloud Armor Edge WAF Security Policy (OWASP Top 10, Adaptive DDoS, Edge Rate Limiting)
resource "google_compute_security_policy" "edge_security_policy" {
  count       = var.enable_load_balancer ? 1 : 0
  project     = var.project_id
  name        = "${var.service_name}-cloud-armor-policy"
  description = "Enterprise Edge WAF policy with OWASP CRS rules, Adaptive DDoS Protection, and IP rate limiting"

  # Rule 1: SQL Injection Protection (OWASP CRS)
  rule {
    action   = "deny(403)"
    priority = "1000"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('sqli-stable', {'sensitivity': 1})"
      }
    }
    description = "Block SQL Injection attacks"
  }

  # Rule 2: Cross-Site Scripting (XSS) Protection (OWASP CRS)
  rule {
    action   = "deny(403)"
    priority = "1100"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('xss-stable', {'sensitivity': 1})"
      }
    }
    description = "Block Cross-Site Scripting (XSS) attacks"
  }

  # Rule 3: Remote File Inclusion (RFI) / Local File Inclusion (LFI)
  rule {
    action   = "deny(403)"
    priority = "1200"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('lfi-stable', {'sensitivity': 1}) || evaluatePreconfiguredWaf('rfi-stable', {'sensitivity': 1})"
      }
    }
    description = "Block Local and Remote File Inclusion attacks"
  }

  # Rule 4: Remote Code Execution (RCE) Protection
  rule {
    action   = "deny(403)"
    priority = "1300"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('rce-stable', {'sensitivity': 1})"
      }
    }
    description = "Block Remote Code Execution attempts"
  }

  # Rule 5: Protocol Attack & Scanner Detection
  rule {
    action   = "deny(403)"
    priority = "1400"
    match {
      expr {
        expression = "evaluatePreconfiguredWaf('protocolattack-stable', {'sensitivity': 1}) || evaluatePreconfiguredWaf('scannerdetection-stable', {'sensitivity': 1})"
      }
    }
    description = "Block HTTP protocol violations and known vulnerability scanners"
  }

  # Rule 6: Edge Rate Limiting (100 requests per minute per client IP)
  rule {
    action   = "rate_based_ban"
    priority = "2000"
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
    description = "Enforce edge rate limiting (100 req/min) to mitigate Layer 7 DDoS and bot flooding"
  }

  # Default Rule: Allow regular traffic
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

  # Adaptive Protection for Layer 7 DDoS Defense
  adaptive_protection_config {
    layer_7_ddos_defense_config {
      enable = true
    }
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
# CDN is disabled on this API backend to prevent accidental caching of private SSO tokens / user responses
resource "google_compute_backend_service" "lb_backend" {
  count           = var.enable_load_balancer ? 1 : 0
  project         = var.project_id
  name            = "${var.service_name}-backend"
  protocol        = "HTTPS"
  port_name       = "http"
  timeout_sec     = 300
  enable_cdn      = false
  security_policy = google_compute_security_policy.edge_security_policy[0].id

  backend {
    group = google_compute_region_network_endpoint_group.serverless_neg[0].id
  }

  depends_on = [google_project_service.services]
}

# 5. Global HTTPS URL Map
resource "google_compute_url_map" "lb_url_map" {
  count           = var.enable_load_balancer ? 1 : 0
  project         = var.project_id
  name            = "${var.service_name}-url-map"
  default_service = google_compute_backend_service.lb_backend[0].id
}

# 6. Google-Managed SSL Certificate for Custom Domain
resource "google_compute_managed_ssl_certificate" "default" {
  count   = var.enable_load_balancer && var.domain_name != "" ? 1 : 0
  project = var.project_id
  name    = "${var.service_name}-ssl-cert"

  managed {
    domains = [var.domain_name]
  }

  depends_on = [google_project_service.services]
}

# 7. Target HTTPS Proxy (Port 443 with Managed SSL)
resource "google_compute_target_https_proxy" "https_proxy" {
  count            = var.enable_load_balancer && var.domain_name != "" ? 1 : 0
  project          = var.project_id
  name             = "${var.service_name}-https-proxy"
  url_map          = google_compute_url_map.lb_url_map[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.default[0].id]
}

# 8. Global Forwarding Rule for HTTPS (Port 443)
resource "google_compute_global_forwarding_rule" "https_forwarding_rule" {
  count                 = var.enable_load_balancer && var.domain_name != "" ? 1 : 0
  project               = var.project_id
  name                  = "${var.service_name}-https-forwarding-rule"
  target                = google_compute_target_https_proxy.https_proxy[0].id
  port_range            = "443"
  ip_address            = google_compute_global_address.lb_ip[0].address
  load_balancing_scheme = "EXTERNAL_MANAGED"

  depends_on = [google_project_service.services]
}

# 9. HTTP-to-HTTPS Redirect URL Map
resource "google_compute_url_map" "http_redirect" {
  count   = var.enable_load_balancer ? 1 : 0
  project = var.project_id
  name    = "${var.service_name}-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }
}

# 10. Target HTTP Proxy for Port 80 Redirect
resource "google_compute_target_http_proxy" "http_redirect_proxy" {
  count   = var.enable_load_balancer ? 1 : 0
  project = var.project_id
  name    = "${var.service_name}-http-redirect-proxy"
  url_map = google_compute_url_map.http_redirect[0].id
}

# 11. Global Forwarding Rule for Port 80 (Enforces 301/308 Redirect to HTTPS)
resource "google_compute_global_forwarding_rule" "http_redirect_rule" {
  count                 = var.enable_load_balancer ? 1 : 0
  project               = var.project_id
  name                  = "${var.service_name}-http-redirect-rule"
  target                = google_compute_target_http_proxy.http_redirect_proxy[0].id
  port_range            = "80"
  ip_address            = google_compute_global_address.lb_ip[0].address
  load_balancing_scheme = "EXTERNAL_MANAGED"

  depends_on = [google_project_service.services]
}
