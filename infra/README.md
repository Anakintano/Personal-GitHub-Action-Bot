# Deploy to GKE

Use Bash, Terraform, Google Cloud CLI, `gke-gcloud-auth-plugin`, and `kubectl`. You need a GCP project with billing enabled, the numeric ID of the GitHub repository that hosts this service, and a DNS name you control. The Terraform and Kubernetes apply commands below create billable infrastructure and are for the operator to run after reviewing the plan.

## Provision infrastructure

From the repository root, export `PROJECT_ID`, `REGION` (for example, `us-central1`), and `REPO_ID`. `gh api repos/OWNER/REPOSITORY --jq .id` returns the delivery repository ID.

```bash
gcloud auth application-default login
export TF_VAR_project_id="$PROJECT_ID" TF_VAR_region="$REGION"
export TF_VAR_github_repository_id="$REPO_ID"

terraform -chdir=infra/bootstrap init
terraform -chdir=infra/bootstrap plan
terraform -chdir=infra/bootstrap apply

STATE_BUCKET=$(terraform -chdir=infra/bootstrap output -raw bucket)
terraform -chdir=infra/gcp init -backend-config="bucket=$STATE_BUCKET"
terraform -chdir=infra/gcp plan
terraform -chdir=infra/gcp apply
```

The separate bootstrap creates a versioned bucket before Terraform initializes the GCS backend. Keep the bootstrap's local state in a secure location. The main stack stores its state and locks in GCS. It provisions a regional Autopilot cluster, a private Artifact Registry repository, an external Ingress IP and two service accounts. GKE uses its node account to pull images. The delivery account pushes images and can locate the cluster. GitHub exchanges a short-lived OIDC token for that identity. The Terraform trust condition accepts `push` and `workflow_dispatch` from the configured repository's `main` branch.

## Prepare the namespace and endpoint

Use an operator identity authorized to install the namespace resources. Export `WEBHOOK_HOST` as a bare DNS name such as `hooks.example.org`.

```bash
gcloud container clusters get-credentials ci-failure-explainer --region "$REGION" --project "$PROJECT_ID"
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/storage.yaml -f k8s/service.yaml
sed "s/PROJECT_ID/$PROJECT_ID/g" k8s/delivery-rbac.yaml | kubectl apply -f -
sed "s/hooks\.example\.com/$WEBHOOK_HOST/g" k8s/ingress.yaml | kubectl apply -f -
terraform -chdir=infra/gcp output -raw ingress_ip
```

Add an A record for `WEBHOOK_HOST` pointing to the output IP. In Cloud Shell or another Linux Bash session, populate the app Secret from your exported environment variables. `k8s/secret.template.yaml` shows the names. The process substitutions support a multiline PEM without saving values to the repository or putting them in command arguments.

```bash
(
  set +x
  set -o pipefail
  : "${NVIDIA_API_KEY:?}" "${GITHUB_APP_ID:?}" "${GITHUB_APP_PRIVATE_KEY:?}" \
    "${GITHUB_APP_BOT_LOGIN:?}" "${GITHUB_WEBHOOK_SECRET:?}" "${GITHUB_INSTALLATION_IDS:?}"
  kubectl -n ci-failure-explainer create secret generic ci-failure-explainer \
    --from-file=NVIDIA_API_KEY=<(printf '%s' "$NVIDIA_API_KEY") \
    --from-file=GITHUB_APP_ID=<(printf '%s' "$GITHUB_APP_ID") \
    --from-file=GITHUB_APP_PRIVATE_KEY=<(printf '%s' "$GITHUB_APP_PRIVATE_KEY") \
    --from-file=GITHUB_APP_BOT_LOGIN=<(printf '%s' "$GITHUB_APP_BOT_LOGIN") \
    --from-file=GITHUB_WEBHOOK_SECRET=<(printf '%s' "$GITHUB_WEBHOOK_SECRET") \
    --from-file=GITHUB_INSTALLATION_IDS=<(printf '%s' "$GITHUB_INSTALLATION_IDS") \
    --from-file=JENKINS_TOKEN=<(printf '%s' "${JENKINS_TOKEN:-}") \
    --from-file=JENKINS_REPOSITORIES=<(printf '%s' "${JENKINS_REPOSITORIES:-}") \
    --dry-run=client -o yaml | kubectl apply --server-side -f -
)
```

Use one deployment pipeline below. After it completes and the Google-managed certificate is `Active`, disable HTTP and configure webhook senders to use `https://$WEBHOOK_HOST/github` and `https://$WEBHOOK_HOST/jenkins`. GKE must program the load balancer before HTTP can be disabled. The Ingress serves only the webhook routes. Health probes use `/healthz`, and `/metrics` remains reachable through the internal Service.

```bash
kubectl -n ci-failure-explainer describe managedcertificate ci-failure-explainer
kubectl -n ci-failure-explainer annotate ingress ci-failure-explainer kubernetes.io/ingress.allow-http=false --overwrite
```

## GitHub Actions delivery

Add these repository Actions variables before dispatching **CI and delivery** on `main`.

| Variable | Value |
| --- | --- |
| `GCP_PROJECT_ID` | `PROJECT_ID` |
| `GCP_REGION` | `REGION` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | `terraform -chdir=infra/gcp output -raw workload_identity_provider` |
| `GCP_DELIVERY_SERVICE_ACCOUNT` | `terraform -chdir=infra/gcp output -raw delivery_service_account` |

The workflow lints and builds PRs. On `main`, it also pushes an image tagged with the commit SHA, applies the Deployment with the pushed digest, and waits for rollout. The delivery identity can manage Deployments in this namespace. The operator manages Secrets, the PVC, and Ingress separately. The app uses one replica with the Recreate strategy and a single-writer disk, so updates briefly interrupt intake. Rotate app secrets in Kubernetes and restart the Deployment from the operator account to pick up new environment values.

## Jenkins delivery

Configure a Jenkins Multibranch Pipeline from this repository with branch discovery restricted to trusted `main` and PR discovery disabled. Use a dedicated Linux agent labeled `gcp-delivery` with Python 3.11, Docker, the Google Cloud CLI, `gke-gcloud-auth-plugin`, and `kubectl`. Attach the Terraform `delivery_service_account` to a GCE agent with the `cloud-platform` OAuth scope and let `gcloud` use the VM identity. Set `GCP_PROJECT_ID` and `GCP_REGION` on the agent. The Jenkinsfile performs the same lint, build, digest push, and rollout steps. Jenkins intake for failures in other repositories is a separate feature of the app.
