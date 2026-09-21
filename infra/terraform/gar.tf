# ==============================================================================
# File: infra/terraform/gar.tf
# Description: Artifact Registry Docker repository for Catz App container images
# ==============================================================================

resource "google_artifact_registry_repository" "repo" {
  repository_id = var.gar_repo_id
  location      = var.region
  format        = "DOCKER"
  description   = "Docker container registry for Catz App (Cloud Run images)"

  docker_config {
    immutable_tags = false
  }
}
