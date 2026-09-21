# ==============================================================================
# File: infra/terraform/jumpbox.tf
# Description: Windows Desktop Jumpbox within the VPC for line-of-sight testing
#              Accessed securely via Google Cloud Identity-Aware Proxy (IAP)
# ==============================================================================

resource "google_service_account" "jumpbox_sa" {
  account_id   = "jumpbox-sa"
  display_name = "Windows Jumpbox Service Account"
}

# Grant Jumpbox permissions to invoke Cloud Run and read GCP resources
resource "google_project_iam_member" "jumpbox_run_invoker" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.jumpbox_sa.email}"
}

resource "google_project_iam_member" "jumpbox_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.jumpbox_sa.email}"
}

# Grant Jumpbox permissions to authenticate to Vertex AI for Gemini agent calls
resource "google_project_iam_member" "jumpbox_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.jumpbox_sa.email}"
}

# Grant Jumpbox permissions to pull container images from Artifact Registry
resource "google_project_iam_member" "jumpbox_gar_reader" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.jumpbox_sa.email}"
}

resource "google_compute_instance" "jumpbox" {
  name         = "browser-jumpbox"
  machine_type = "e2-standard-4"
  zone         = var.zone
  tags         = ["jumpbox"]

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = 50
      type  = "pd-ssd"
    }
  }

  network_interface {
    subnetwork = google_compute_subnetwork.subnet.id
    # Private IP only. Inbound via IAP, outbound via Cloud NAT.
  }

  service_account {
    email  = google_service_account.jumpbox_sa.email
    scopes = ["cloud-platform"]
  }

  shielded_instance_config {
    enable_secure_boot          = true
    enable_vtpm                 = true
    enable_integrity_monitoring = true
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -e
    export DEBIAN_FRONTEND=noninteractive

    # Install network diagnostic tools for DNS, routing, and HTTP testing
    apt-get update
    apt-get install -y ca-certificates curl gnupg lsb-release dnsutils iputils-ping net-tools traceroute jq

    # Install Docker
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io

    systemctl start docker
    systemctl enable docker

    # Launch Web-based Chromium container accessible over HTML5 on port 3000
    docker run -d \
      --name=web-browser \
      --restart=always \
      --security-opt seccomp=unconfined \
      -e PUID=1000 \
      -e PGID=1000 \
      -e TZ=America/Toronto \
      -p 3000:3000 \
      lscr.io/linuxserver/chromium:latest
  EOT

  depends_on = [
    google_compute_router_nat.nat
  ]
}
