# ==============================================================================
# File: infra/terraform/outputs.tf
# Description: Outputs for Catz App infrastructure
# ==============================================================================

output "artifact_registry_url" {
  description = "Docker repository URL for pushing images"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.repo.repository_id}"
}

output "vpc_name" {
  description = "Standalone isolated VPC Name"
  value       = google_compute_network.vpc.name
}

output "subnet_name" {
  description = "Primary Subnet Name"
  value       = google_compute_subnetwork.subnet.name
}

output "jumpbox_name" {
  description = "Linux Browser Jumpbox VM instance name"
  value       = google_compute_instance.jumpbox.name
}

output "jumpbox_private_ip" {
  description = "Internal IP address of Jumpbox"
  value       = google_compute_instance.jumpbox.network_interface[0].network_ip
}

output "jumpbox_web_tunnel_command" {
  description = "Command to tunnel port 3000 for in-browser Chromium Web GUI"
  value       = "gcloud compute start-iap-tunnel ${google_compute_instance.jumpbox.name} 3000 --local-host-port=localhost:3000 --zone=${var.zone} --project=${var.project_id}"
}

output "jumpbox_ssh_command" {
  description = "Command to SSH into Jumpbox for DNS and network diagnostic tests"
  value       = "gcloud compute ssh ${google_compute_instance.jumpbox.name} --zone=${var.zone} --project=${var.project_id}"
}

output "game_server_url" {
  description = "Game Server Cloud Run URL"
  value       = google_cloud_run_v2_service.game_server.uri
}

output "cat_agent_url" {
  description = "Cat Agent Workbench Cloud Run URL"
  value       = google_cloud_run_v2_service.cat_agent.uri
}

output "internal_gateway_ip" {
  description = "Internal Private IP address in catz-vpc-subnet for Cloud Run endpoints"
  value       = google_compute_address.internal_gateway_ip.address
}

output "game_server_internal_dns" {
  description = "Internal Private DNS hostname for Game Server"
  value       = "http://game.catz.internal"
}

output "cat_agent_internal_dns" {
  description = "Internal Private DNS hostname for Cat Agent Workbench"
  value       = "http://agent.catz.internal"
}
