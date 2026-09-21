# ==============================================================================
# File: infra/terraform/cloud_run.tf
# Description: Cloud Run v2 Services with Direct VPC Egress into catz-subnet
# ==============================================================================

# Runtime Service Account for Cloud Run (Vertex AI access)
resource "google_service_account" "cloud_run_sa" {
  account_id   = "catz-cloudrun-sa"
  display_name = "Catz App Cloud Run Runtime SA"
}

# Grant Vertex AI user role for Gemini LLM calls
resource "google_project_iam_member" "cloud_run_ai_user" {
  project = var.project_id
  role    = "roles/aiplatform.user"
  member  = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# ----------------------------------------------------
# 1. Game Server Cloud Run Service
# ----------------------------------------------------
resource "google_cloud_run_v2_service" "game_server" {
  name     = "game-server"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.cloud_run_sa.email

    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.subnet.id
      }
      egress = "ALL_TRAFFIC"
    }

    containers {
      image = var.game_server_image

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }
    }
  }
}

# Allow domain invocation (adheres to org domain-restricted sharing policy)
resource "google_cloud_run_v2_service_iam_member" "game_server_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.game_server.name
  role     = "roles/run.invoker"
  member   = "domain:shanebaccas.altostrat.com"
}

resource "google_cloud_run_v2_service_iam_member" "game_server_invoker_jumpbox" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.game_server.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.jumpbox_sa.email}"
}

resource "google_cloud_run_v2_service_iam_member" "game_server_invoker_cloud_run" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.game_server.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.cloud_run_sa.email}"
}

# ----------------------------------------------------
# 2. Cat Agent Cloud Run Service
# ----------------------------------------------------
resource "google_cloud_run_v2_service" "cat_agent" {
  name     = "cat-agent"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.cloud_run_sa.email

    vpc_access {
      network_interfaces {
        network    = google_compute_network.vpc.id
        subnetwork = google_compute_subnetwork.subnet.id
      }
      egress = "ALL_TRAFFIC"
    }

    containers {
      image = var.cat_agent_image

      ports {
        container_port = 8001
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_REGION"
        value = var.region
      }
      env {
        name  = "GOOGLE_GENAI_USE_VERTEXAI"
        value = "true"
      }
      env {
        name  = "GAME_SERVER_URL"
        value = "http://game.catz.internal"
      }
      env {
        name  = "GAME_SERVER_AUDIENCE"
        value = google_cloud_run_v2_service.game_server.uri
      }

      resources {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }
    }
  }
}

resource "google_cloud_run_v2_service_iam_member" "cat_agent_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.cat_agent.name
  role     = "roles/run.invoker"
  member   = "domain:shanebaccas.altostrat.com"
}

resource "google_cloud_run_v2_service_iam_member" "cat_agent_invoker_jumpbox" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.cat_agent.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.jumpbox_sa.email}"
}
