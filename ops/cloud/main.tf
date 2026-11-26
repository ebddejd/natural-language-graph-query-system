terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}
variable "project" { type = string }
variable "region" {
  type    = string
  default = "northamerica-northeast1"
}
variable "image" {
  type        = string
  description = "Immutable image URL with @sha256 digest. Leave empty to provision the registry only."
  default     = ""
  validation {
    condition     = var.image == "" || can(regex("@sha256:[a-f0-9]{64}$", var.image))
    error_message = "Supply an image digest or leave empty for the registry bootstrap."
  }
}
variable "public_url" {
  type    = string
  default = ""
}
variable "model" {
  type    = string
  default = ""
}
variable "secret_ids" {
  type        = map(string)
  default     = {}
  description = "Existing Secret Manager IDs keyed by GEOAPIFY_API_KEY and LLM_API_KEY."
}
provider "google" {
  project = var.project
  region  = var.region
}
resource "google_artifact_registry_repository" "journey" {
  location      = var.region
  repository_id = "journey-evaluation"
  format        = "DOCKER"
}
resource "google_service_account" "journey" {
  account_id = "journey-evaluation"
}
resource "google_secret_manager_secret_iam_member" "read" {
  for_each  = var.secret_ids
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.journey.email}"
}
resource "google_cloud_run_v2_service" "journey" {
  count               = var.image == "" ? 0 : 1
  name                = "journey-evaluation"
  location            = var.region
  deletion_protection = false
  depends_on          = [google_secret_manager_secret_iam_member.read]
  lifecycle {
    precondition {
      condition     = startswith(var.public_url, "https://") && var.model != "" && alltrue([for k in ["LLM_API_KEY", "GEOAPIFY_API_KEY"] : contains(keys(var.secret_ids), k)])
      error_message = "Set the canonical HTTPS URL, model name and both provider secret IDs."
    }
  }
  template {
    service_account = google_service_account.journey.email
    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }
    containers {
      image = var.image
      ports { container_port = 10000 }
      env {
        name  = "PUBLIC_URL"
        value = var.public_url
      }
      env {
        name  = "LLM_MODEL"
        value = var.model
      }
      dynamic "env" {
        for_each = var.secret_ids
        content {
          name = env.key
          value_source {
            secret_key_ref {
              secret  = env.value
              version = "latest"
            }
          }
        }
      }
      startup_probe {
        http_get {
          path = "/healthz"
          port = 10000
        }
      }
    }
  }
}
output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project}/${google_artifact_registry_repository.journey.repository_id}"
}
