terraform {
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 7.0"
    }
  }

  backend "gcs" {
    prefix = "ci-failure-explainer"
  }
}

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "github_repository_id" {
  description = "Numeric ID of the GitHub repository authorized to deploy this service."
  type        = string

  validation {
    condition     = can(regex("^[0-9]+$", var.github_repository_id))
    error_message = "Set github_repository_id to the numeric GitHub repository ID."
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "apis" {
  for_each = toset([
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "sts.googleapis.com",
  ])

  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "gke" {
  name                    = "ci-failure-explainer"
  auto_create_subnetworks = false
  depends_on              = [google_project_service.apis]
}

resource "google_compute_subnetwork" "gke" {
  name          = "ci-failure-explainer"
  network       = google_compute_network.gke.id
  ip_cidr_range = "10.10.0.0/24"

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = "10.20.0.0/16"
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = "10.30.0.0/20"
  }
}

resource "google_service_account" "nodes" {
  account_id   = "ci-explainer-nodes"
  display_name = "CI Failure Explainer GKE nodes"
  depends_on   = [google_project_service.apis]
}

resource "google_project_iam_member" "nodes" {
  project = var.project_id
  role    = "roles/container.defaultNodeServiceAccount"
  member  = google_service_account.nodes.member
}

resource "google_container_cluster" "gke" {
  name                = "ci-failure-explainer"
  location            = var.region
  enable_autopilot    = true
  deletion_protection = true
  network             = google_compute_network.gke.id
  subnetwork          = google_compute_subnetwork.gke.id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.nodes.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }

  depends_on = [google_project_iam_member.nodes]
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "ci-failure-explainer"
  format        = "DOCKER"
  depends_on    = [google_project_service.apis]
}

resource "google_artifact_registry_repository_iam_member" "pull" {
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.reader"
  member     = google_service_account.nodes.member
}

resource "google_compute_global_address" "ingress" {
  name       = "ci-explainer-ingress"
  depends_on = [google_project_service.apis]
}
