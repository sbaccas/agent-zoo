# ==============================================================================
# File: infra/terraform/vpc.tf
# Description: Fully isolated, standalone VPC network, subnet, Cloud NAT, and firewalls
#              (Zero shared VPC, 100% self-contained)
# ==============================================================================

resource "google_compute_network" "vpc" {
  name                    = var.vpc_name
  auto_create_subnetworks = false
  description             = "Standalone isolated VPC for Catz App Prod"
}

resource "google_compute_subnetwork" "subnet" {
  name                     = "${var.vpc_name}-subnet"
  ip_cidr_range            = var.subnet_cidr
  region                   = var.region
  network                  = google_compute_network.vpc.id
  private_ip_google_access = true
  description              = "Primary subnet for Jumpbox and Cloud Run Direct VPC Egress"
}

# Cloud Router for NAT
resource "google_compute_router" "router" {
  name    = "${var.vpc_name}-router"
  region  = var.region
  network = google_compute_network.vpc.id
}

# Cloud NAT: Allows private VM to reach internet (downloads, updates) without public IP
resource "google_compute_router_nat" "nat" {
  name                               = "${var.vpc_name}-nat"
  router                             = google_compute_router.router.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

# Firewall: Allow IAP Tunneling for SSH (22), Web GUI (3000), RDP (3389)
resource "google_compute_firewall" "allow_iap" {
  name    = "${var.vpc_name}-allow-iap"
  network = google_compute_network.vpc.id

  allow {
    protocol = "tcp"
    ports    = ["22", "3000", "3389"]
  }

  source_ranges = ["35.235.240.0/20"]
  target_tags   = ["jumpbox"]
  description   = "Allow Identity-Aware Proxy (IAP) to tunnel SSH, Web GUI, and RDP to Jumpbox"
}

# Firewall: Allow internal VPC traffic
resource "google_compute_firewall" "allow_internal" {
  name    = "${var.vpc_name}-allow-internal"
  network = google_compute_network.vpc.id

  allow {
    protocol = "all"
  }

  source_ranges = [var.subnet_cidr]
  description   = "Allow internal traffic within subnet"
}
