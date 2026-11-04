# CI Failure Explainer

Find out why a PR failed without reading the entire CI log. The GitHub App keeps one diagnosis comment on each PR and edits it to `✅ fixed in <sha>` when the workflow that owns the comment succeeds on the current PR head. Jenkins gets the same diagnosis in the build console.

## Run locally

With Docker running, copy `.env.example` to `.env` and run `docker compose up --build`. The application is at [localhost:8000/healthz](http://localhost:8000/healthz), Prometheus at [localhost:9090](http://localhost:9090), and the provisioned dashboard at [localhost:3000/d/ci-failure-explainer](http://localhost:3000/d/ci-failure-explainer). Credentials can stay empty for local startup. Supply them before sending actual events.

To run just the app with Python 3.11, install `requirements.txt`, run `python ml/train.py`, set configuration in your environment, and run `python -m uvicorn app:app`. Compose reads `.env`; Uvicorn does not load it automatically. Use one worker and one replica with the SQLite volume.

## Install on a GitHub repository

1. [Register a GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app). Point its webhook URL at your HTTPS service's `/github` route and set a webhook secret. For local development the URL must forward to port 8000 and be reachable by GitHub.
2. Set repository permissions to **Actions: Read-only**, **Contents: Read-only**, and **Pull requests: Read and write**. Subscribe to **Workflow run**. Generate a private key and install the app on the selected base repository.
3. Set `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (PEM, with literal `\n` accepted), `GITHUB_APP_BOT_LOGIN` (the App slug plus `[bot]`), and `GITHUB_WEBHOOK_SECRET`. Set `GITHUB_INSTALLATION_IDS` to the comma-separated numeric IDs from the installation settings URL. Set `NVIDIA_API_KEY`. Keep these in local environment variables or the Kubernetes Secret.
4. Use a workflow that runs for pull requests. On a failure, open the PR to see the diagnosis. Rerun the workflow after fixing the failure and watch the same comment change. Run attempt is part of the delivery key because GitHub reuses the run ID on a rerun.

## Report a Jenkins failure

Set `JENKINS_TOKEN` and a comma-separated `JENKINS_REPOSITORIES` allowlist on the service. In Jenkins, bind the same token from Credentials and set `EXPLAINER_URL` and `EXPLAINER_REPOSITORY` (for example, `owner/repo`). If the failing command writes `build.log`, add this to your Declarative Pipeline. The hook sends up to the last 1 MB and prints the JSON diagnosis to the build console. The endpoint has no GitHub dependency.

```groovy
post {
    failure {
        sh 'set +x; test -s build.log && tail -c 1000000 build.log | curl -fsS -H "Authorization: Bearer $JENKINS_TOKEN" -H "X-Repository: $EXPLAINER_REPOSITORY" -H "X-Build-ID: $BUILD_TAG" -H "Content-Type: text/plain" --data-binary @- "$EXPLAINER_URL/jenkins"'
    }
}
```

## How it works

1. `app.py` verifies the GitHub signature against the raw request and checks the installation allowlist. It acknowledges the webhook and processes completed failures and successes in the background. Jenkins authenticates with a shared bearer token and uses the same analysis pipeline.
2. `github_client.py` requests a token scoped to the base repository. It finds the open PR by head SHA, including forks, and skips ambiguous matches. It asks GitHub for jobs in the specific run attempt and downloads logs from failed jobs only. GitHub documents [the direct step endpoint for API version 2026-03-10](https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2026-03-10#download-step-logs-for-a-workflow-run-job), but RepLift returned 404 for it. The verified fallback downloads the failed job log and keeps entries in the failed step's time window. Signed downloads receive no installation token.
3. `log_processor.py` removes timestamps and ANSI codes, redacts known credential patterns, keeps error matches with up to 50 lines of context on either side, and falls back to the last 50 lines when no match exists. The aggregate log budget is 24,000 UTF-8 bytes, roughly 8,000 tokens. Redaction is pattern based and cannot recognize every secret format.
4. `analyze.py` sends the first matching YAML rule and the classifier's predicted category and probability as hints in one request to NVIDIA's hosted [Nemotron 3.5 Bolt E4B](https://build.nvidia.com/nvidia/nemotron-3.5-bolt-e4b) free endpoint. `LLM_MODEL` defaults to the live-tested `nvidia/nemotron-3.5-bolt-e4b` alias. The request asks for structured JSON with a 1,024-token output cap. The service validates the six fields and requires a complete error line from the named step, plus confidence of at least 0.7. It reports **unsure** when that check fails. The classifier footer appears at scores of 50% or higher. That score is separate from the diagnosis confidence.

Before a live demo, send one synthetic log through the service and confirm the provider returns a diagnosis with an exact evidence line. Keep the [saved RepLift screenshots](README.md#live-demo) available if the free endpoint is unavailable.
5. `store.py` records the analysis before publishing, so a redelivery can reuse it if comment publishing failed. It deduplicates by repository, run ID and attempt (or Jenkins build ID), and enforces a shared per-repository UTC daily budget of 20 by default. The GitHub client updates only an App-authored comment with the project marker. A successful run edits an existing comment for the same workflow. An older run of that workflow cannot replace a newer comment.

The hosted model has no tools, secrets, or permission to change anything. It turns evidence into a diagnosis. The code decides whether to publish and performs every side effect. GitHub and the hosted model HTTP 429 responses get up to three retries with backoff. If log retrieval or the hosted model still fails, the app posts **Analysis unavailable** when GitHub's comment API is reachable.

The webhook has no polling or cron. The service saves each accepted GitHub event to SQLite before responding and replays pending events at startup. Use the GitHub App's recent deliveries to retry a failed delivery while the service is running. A crash between the hosted model responding and SQLite saving the result can cause one more request on redelivery. One replica serializes comment creation because GitHub offers no atomic comment upsert.

## Classifier evaluation

`ml/data/seed.csv` contains 120 **synthetic** examples, 20 per category. On a stratified 75/25 split with seed 42, logistic regression with `C=10` and `max_iter=1000` scored **73.3% held-out accuracy (22 of 30)**. That is one extra correct prediction on the same synthetic holdout. On the saved RepLift log, the `lint/type` score rose from **28.0% to 57.7%**. This is a sharper model score, with no demonstrated improvement in calibration or accuracy on real CI runs.

| Actual ↓ / predicted → | Dependency | Test | Lint/type | Build/compile | Config-secret | Infra-flaky |
|---|---:|---:|---:|---:|---:|---:|
| Dependency | 4 | 0 | 0 | 1 | 0 | 0 |
| Test | 1 | 3 | 0 | 0 | 0 | 1 |
| Lint/type | 0 | 1 | 3 | 1 | 0 | 0 |
| Build/compile | 0 | 0 | 0 | 5 | 0 | 0 |
| Config-secret | 0 | 0 | 0 | 1 | 4 | 0 |
| Infra-flaky | 0 | 0 | 0 | 1 | 1 | 3 |

The trainer refits TF-IDF and logistic regression on all seed rows after evaluation. Run `python ml/train.py --db /path/to/explainer.sqlite` to include collected, redacted log excerpts with accepted labels. These are model-derived labels and can carry errors into the next training run. The six-class trainer skips unsure results and the rule-only `oom` category. The Docker build bakes in a locally generated `ml/model.joblib` if present, or trains from seed. Keep the database out of the image and load model files only from trusted builds.

## Monitoring and deployment

Prometheus scrapes `/metrics`. The dashboard shows accepted diagnoses by category and hint source, LLM tokens and latency, rate limits, and analysis errors. Hint source identifies agreement with rules first, then ML, and otherwise the LLM category. See the [GCP runbook](infra/README.md) for Terraform state and GKE setup, the Kubernetes Secret, and delivery from GitHub Actions or Jenkins.

Run the regression tests with `python -m unittest discover -s tests -v`. See [TESTING_TODO.md](TESTING_TODO.md) for remaining coverage.

The file structure follows the DevOpsBoys [CI/CD failure analyzer tutorial](https://devopsboys.com/blog/build-cicd-failure-analyzer-langchain-2026). The implementation is original.
