pipeline {
    agent { label 'gcp-delivery' }
    options { disableConcurrentBuilds() }

    stages {
        stage('Lint') {
            steps {
                sh '''
                    python3.11 -m venv .venv
                    .venv/bin/python -m pip install ruff
                    .venv/bin/ruff check .
                '''
            }
        }
        stage('Build') {
            steps {
                sh 'docker build --tag "ci-failure-explainer:$GIT_COMMIT" .'
            }
        }
        stage('Push and deploy') {
            when { branch 'main' }
            steps {
                sh '''#!/usr/bin/env bash
set -euo pipefail
: "${GCP_PROJECT_ID:?set GCP_PROJECT_ID on the Jenkins agent}"
region="${GCP_REGION:-us-central1}"
registry="$region-docker.pkg.dev"
image="$registry/$GCP_PROJECT_ID/ci-failure-explainer/app:$GIT_COMMIT"
tmp=$(mktemp -d)
trap 'rm -f "$tmp/config.json" "$tmp/kubeconfig"; rmdir "$tmp"' EXIT
export DOCKER_CONFIG="$tmp" KUBECONFIG="$tmp/kubeconfig"
gcloud auth configure-docker "$registry" --quiet
docker tag "ci-failure-explainer:$GIT_COMMIT" "$image"
docker push "$image"
gcloud container clusters get-credentials ci-failure-explainer --region "$region" --project "$GCP_PROJECT_ID"
bash infra/deploy.sh "$(docker image inspect --format '{{index .RepoDigests 0}}' "$image")"
                '''
            }
        }
    }
}
