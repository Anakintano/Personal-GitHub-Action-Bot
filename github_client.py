import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx
import jwt

from metrics import RATE_LIMITED

COMMENT_MARKER = "<!-- ci-failure-explainer -->"
API_VERSION = "2026-03-10"


def retry_429(headers: httpx.Headers, attempt: int) -> bool:
    RATE_LIMITED.inc()
    delay = headers.get("Retry-After", "")
    delay = (
        int(delay) if delay.isascii() and delay.isdecimal() and len(delay) < 10 else 0
    )
    if attempt == 3 or delay > 60:
        return False
    time.sleep(max(delay, 2**attempt))
    return True


def installation_token(
    app_id: str, private_key: str, installation_id: int, repo_id: int
) -> str:
    now = int(time.time())
    assertion = jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": app_id},
        private_key,
        algorithm="RS256",
    )
    response = GitHubClient._request(
        "POST",
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {assertion}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        },
        json={"repository_ids": [repo_id]},
    )
    response.raise_for_status()
    token = response.json()["token"]
    if not isinstance(token, str) or not token:
        raise ValueError("GitHub returned an invalid installation token")
    return token


class GitHubClient:
    def __init__(self, token: str, repository: str, bot_login: str):
        self.repo = f"https://api.github.com/repos/{repository}"
        self.bot_login = bot_login
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
        }

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return self._request(
            method, f"{self.repo}/{path}", headers=self.headers, **kwargs
        )

    @staticmethod
    def _request(method: str, url: str, **kwargs) -> httpx.Response:
        for attempt in range(4):
            response = httpx.request(method, url, timeout=30, **kwargs)
            if response.status_code != 429 or not retry_429(response.headers, attempt):
                return response
        raise RuntimeError("GitHub retry loop exhausted")

    def pages(self, path: str, *, key: str | None = None, **params) -> Iterator[dict]:
        page = 1
        while True:
            response = self.request(
                "GET", path, params={**params, "per_page": 100, "page": page}
            )
            response.raise_for_status()
            data = response.json()
            yield from data[key] if key else data
            if "next" not in response.links:
                return
            page += 1

    def find_pr(self, sha: str) -> int | None:
        matches = {
            pr["number"]
            for pr in self.pages("pulls", state="open")
            if pr["head"]["sha"] == sha
        }
        return next(iter(matches)) if len(matches) == 1 else None

    def is_current_head(self, pr: int, sha: str) -> bool:
        response = self.request("GET", f"pulls/{pr}")
        response.raise_for_status()
        pull = response.json()
        return pull["state"] == "open" and pull["head"]["sha"] == sha

    def failed_steps(
        self, run_id: int, attempt: int
    ) -> Iterator[tuple[int, dict, str]]:
        path = f"actions/runs/{run_id}/attempts/{attempt}/jobs"
        for job in self.pages(path, key="jobs"):
            if job["conclusion"] == "failure":
                for step in job["steps"]:
                    if step["conclusion"] == "failure":
                        yield (
                            job["id"],
                            step,
                            f"{job['name']} / {step['name']}",
                        )

    def step_log(self, job_id: int, step: dict) -> str:
        response = self.request(
            "GET", f"actions/jobs/{job_id}/steps/{step['number']}/logs"
        )
        if response.status_code != 404:
            return self.download_log(response)

        # The step route can be unavailable even when the failed job log exists.
        start = datetime.fromisoformat(step["started_at"])
        end = datetime.fromisoformat(step["completed_at"]) + timedelta(seconds=1)
        log = self.download_log(self.request("GET", f"actions/jobs/{job_id}/logs"))
        selected = []
        for line in log.splitlines():
            timestamp, _, _ = line.partition(" ")
            try:
                at = datetime.fromisoformat(timestamp)
                if start <= at < end:
                    selected.append(line)
            except (ValueError, TypeError):
                continue
        return "\n".join(selected)

    def download_log(self, response: httpx.Response) -> str:
        if response.status_code != 302:
            response.raise_for_status()
            raise ValueError("Expected a GitHub log download redirect")
        url = response.headers.get("Location", "")
        target = urlsplit(url)
        if (
            target.scheme != "https"
            or not target.hostname
            or target.username is not None
        ):
            raise ValueError("Invalid GitHub log download URL")
        # The signed download URL does not need the installation token.
        download = self._request("GET", url)
        if not download.is_success:
            raise ValueError(
                f"GitHub log download returned HTTP {download.status_code}"
            )
        return download.text

    def upsert_comment(
        self,
        pr: int,
        workflow_id: int,
        run_number: int,
        attempt: int,
        body: str,
        *,
        fixed: bool,
    ) -> None:
        workflow_marker = f"{COMMENT_MARKER}\n<!-- workflow:{workflow_id}:"
        prefix = f"{workflow_marker}{run_number}:{attempt} -->\n"
        path = f"issues/{pr}/comments"
        for comment in self.pages(path):
            if (comment.get("user") or {}).get("login") == self.bot_login and (
                comment.get("body") or ""
            ).startswith(f"{COMMENT_MARKER}\n"):
                old_body = comment["body"]
                if old_body.startswith(workflow_marker):
                    order = old_body[len(workflow_marker) :].split(" -->", 1)[0]
                    old_run, old_attempt = map(int, order.split(":"))
                    if (old_run, old_attempt) > (run_number, attempt):
                        return
                elif fixed and not old_body.startswith(
                    f"{COMMENT_MARKER}\n<!-- workflow:{workflow_id} -->\n"
                ):
                    return
                self.request(
                    "PATCH",
                    f"issues/comments/{comment['id']}",
                    json={"body": prefix + body},
                ).raise_for_status()
                return
        if not fixed:
            self.request("POST", path, json={"body": prefix + body}).raise_for_status()
