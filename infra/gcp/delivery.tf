resource "google_service_account" "delivery" {
  account_id   = "ci-explainer-delivery"
  display_name = "CI Failure Explainer delivery"
  depends_on   = [google_project_service.apis]
}

resource "google_artifact_registry_repository_iam_member" "push" {
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = google_service_account.delivery.member
}

resource "google_project_iam_member" "get_cluster" {
  project = var.project_id
  role    = "roles/container.clusterViewer"
  member  = google_service_account.delivery.member
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "ci-explainer"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github"
  attribute_condition                = "assertion.repository_id == '${var.github_repository_id}' && assertion.ref == 'refs/heads/main' && (assertion.event_name == 'push' || assertion.event_name == 'workflow_dispatch')"

  attribute_mapping = {
    "google.subject"          = "assertion.sub"
    "attribute.repository_id" = "assertion.repository_id"
  }

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github" {
  service_account_id = google_service_account.delivery.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
}

output "delivery_service_account" {
  value = google_service_account.delivery.email
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}

output "registry" {
  value = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "ingress_ip" {
  value = google_compute_global_address.ingress.address
}
