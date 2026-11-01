# ==============================================================================
# File: infra/terraform/variables.tf
# Description: Input variables for Catz App infrastructure
# ==============================================================================

variable "project_id" {
  type        = string
  description = "The GCP Project ID"
}

variable "region" {
  type        = string
  description = "GCP Region (Canada Central)"
  default     = "northamerica-northeast1"
}

variable "zone" {
  type        = string
  description = "GCP Zone within region"
  default     = "northamerica-northeast1-a"
}

variable "vpc_name" {
  type        = string
  description = "Name of the isolated standalone VPC"
  default     = "catz-vpc"
}

variable "subnet_cidr" {
  type        = string
  description = "CIDR block for the primary subnet"
  default     = "10.10.0.0/24"
}

variable "gar_repo_id" {
  type        = string
  description = "Artifact Registry Docker repository ID"
  default     = "catz-repo"
}

variable "windows_machine_type" {
  type        = string
  description = "Machine type for the Windows Desktop jumpbox"
  default     = "e2-standard-4"
}

variable "game_server_image" {
  type        = string
  description = "Container image for Game Server (defaults to GAR placeholder if not yet pushed)"
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "cat_agent_image" {
  type        = string
  description = "Container image for Cat Agent (defaults to GAR placeholder if not yet pushed)"
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "mcp_server_image" {
  type        = string
  description = "Container image for Cat & Mouse MCP Server (defaults to GAR placeholder if not yet pushed)"
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "allowed_domain" {
  type        = string
  description = "Authorized Google Workspace domain for Cloud Run IAM invoker bindings"
  default     = "your-organization.example.com"
}
