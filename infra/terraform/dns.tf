# ==============================================================================
# File: infra/terraform/dns.tf
# Description: Cloud DNS Private Zone, Internal Ingress Gateway (Serverless NEGs),
#              and Private Subnet IP endpoints for Cloud Run stateful instances.
# ==============================================================================

# 1. Proxy-only subnet (mandatory for GCP Regional Internal Application Load Balancer / Gateway)
resource "google_compute_subnetwork" "proxy_only_subnet" {
  name          = "${var.vpc_name}-proxy-subnet"
  ip_cidr_range = "10.10.1.0/24"
  region        = var.region
  network       = google_compute_network.vpc.id
  purpose       = "REGIONAL_MANAGED_PROXY"
  role          = "ACTIVE"
}

# 2. Serverless Network Endpoint Groups (NEGs) for Cloud Run Services
resource "google_compute_region_network_endpoint_group" "game_server_neg" {
  name                  = "game-server-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region

  cloud_run {
    service = google_cloud_run_v2_service.game_server.name
  }
}

resource "google_compute_region_network_endpoint_group" "cat_agent_neg" {
  name                  = "cat-agent-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region

  cloud_run {
    service = google_cloud_run_v2_service.cat_agent.name
  }
}

resource "google_compute_region_network_endpoint_group" "cat_agent_2_neg" {
  name                  = "cat-agent-2-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region

  cloud_run {
    service = google_cloud_run_v2_service.cat_agent_2.name
  }
}

resource "google_compute_region_network_endpoint_group" "mcp_server_neg" {
  name                  = "mcp-server-neg"
  network_endpoint_type = "SERVERLESS"
  region                = var.region

  cloud_run {
    service = google_cloud_run_v2_service.mcp_server.name
  }
}

# 3. Regional Backend Services
resource "google_compute_region_backend_service" "game_server_backend" {
  name                  = "game-server-backend"
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group          = google_compute_region_network_endpoint_group.game_server_neg.id
    balancing_mode = "UTILIZATION"
  }
}

resource "google_compute_region_backend_service" "cat_agent_backend" {
  name                  = "cat-agent-backend"
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group          = google_compute_region_network_endpoint_group.cat_agent_neg.id
    balancing_mode = "UTILIZATION"
  }
}

resource "google_compute_region_backend_service" "cat_agent_2_backend" {
  name                  = "cat-agent-2-backend"
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group          = google_compute_region_network_endpoint_group.cat_agent_2_neg.id
    balancing_mode = "UTILIZATION"
  }
}

resource "google_compute_region_backend_service" "mcp_server_backend" {
  name                  = "mcp-server-backend"
  region                = var.region
  protocol              = "HTTP"
  load_balancing_scheme = "INTERNAL_MANAGED"

  backend {
    group          = google_compute_region_network_endpoint_group.mcp_server_neg.id
    balancing_mode = "UTILIZATION"
  }
}

# 4. Regional URL Map for host-based routing (game vs agent1 vs agent2 vs mcp)
resource "google_compute_region_url_map" "internal_url_map" {
  name            = "catz-internal-url-map"
  region          = var.region
  default_service = google_compute_region_backend_service.game_server_backend.id

  host_rule {
    hosts        = ["game.catz.internal", "game-server.catz.internal"]
    path_matcher = "game-matcher"
  }

  host_rule {
    hosts        = ["agent.catz.internal", "cat-agent.catz.internal", "cat1.catz.internal"]
    path_matcher = "agent-matcher"
  }

  host_rule {
    hosts        = ["cat2.catz.internal", "cat-agent-2.catz.internal", "agent2.catz.internal"]
    path_matcher = "agent-2-matcher"
  }

  host_rule {
    hosts        = ["mcp.catz.internal", "mcp-server.catz.internal"]
    path_matcher = "mcp-matcher"
  }

  path_matcher {
    name            = "game-matcher"
    default_service = google_compute_region_backend_service.game_server_backend.id
  }

  path_matcher {
    name            = "agent-matcher"
    default_service = google_compute_region_backend_service.cat_agent_backend.id
  }

  path_matcher {
    name            = "agent-2-matcher"
    default_service = google_compute_region_backend_service.cat_agent_2_backend.id
  }

  path_matcher {
    name            = "mcp-matcher"
    default_service = google_compute_region_backend_service.mcp_server_backend.id
  }
}

# 5. Regional Target HTTP Proxy
resource "google_compute_region_target_http_proxy" "internal_http_proxy" {
  name    = "catz-internal-http-proxy"
  region  = var.region
  url_map = google_compute_region_url_map.internal_url_map.id
}

# 6. Static Private IP Address consumed from catz-vpc-subnet
resource "google_compute_address" "internal_gateway_ip" {
  name         = "catz-internal-gateway-ip"
  subnetwork   = google_compute_subnetwork.subnet.id
  address_type = "INTERNAL"
  region       = var.region
}

# 7. Regional Forwarding Rule (Private Endpoint on port 80)
resource "google_compute_forwarding_rule" "internal_forwarding_rule" {
  name                  = "catz-internal-forwarding-rule"
  region                = var.region
  load_balancing_scheme = "INTERNAL_MANAGED"
  network               = google_compute_network.vpc.id
  subnetwork            = google_compute_subnetwork.subnet.id
  ip_address            = google_compute_address.internal_gateway_ip.address
  ip_protocol           = "TCP"
  port_range            = "80"
  target                = google_compute_region_target_http_proxy.internal_http_proxy.id
  depends_on            = [google_compute_subnetwork.proxy_only_subnet]
}

# 8. Cloud DNS Private Zone for catz.internal
resource "google_dns_managed_zone" "catz_internal" {
  name        = "catz-internal"
  dns_name    = "catz.internal."
  description = "Private DNS Zone for internal VPC Cloud Run endpoints"
  visibility  = "private"

  private_visibility_config {
    networks {
      network_url = google_compute_network.vpc.id
    }
  }
}

# 9. Cloud DNS Record Sets
resource "google_dns_record_set" "game_dns" {
  name         = "game.catz.internal."
  managed_zone = google_dns_managed_zone.catz_internal.name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.internal_gateway_ip.address]
}

resource "google_dns_record_set" "agent_dns" {
  name         = "agent.catz.internal."
  managed_zone = google_dns_managed_zone.catz_internal.name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.internal_gateway_ip.address]
}

resource "google_dns_record_set" "cat1_dns" {
  name         = "cat1.catz.internal."
  managed_zone = google_dns_managed_zone.catz_internal.name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.internal_gateway_ip.address]
}

resource "google_dns_record_set" "cat2_dns" {
  name         = "cat2.catz.internal."
  managed_zone = google_dns_managed_zone.catz_internal.name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.internal_gateway_ip.address]
}

resource "google_dns_record_set" "mcp_dns" {
  name         = "mcp.catz.internal."
  managed_zone = google_dns_managed_zone.catz_internal.name
  type         = "A"
  ttl          = 300
  rrdatas      = [google_compute_address.internal_gateway_ip.address]
}
