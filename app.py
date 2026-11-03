import hmac
import logging
import os
import re
from asyncio import Lock, create_task
from contextlib import asynccontextmanager
from typing import Annotated

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import (
    BaseModel,
    ConfigDict,
    PositiveInt,
    StringConstraints,
    ValidationError,
)

import store
from analyze import UNAVAILABLE, Analysis, analyze, collect_failure_logs
from comment_formatter import format_fixed
from github_client import GitHubClient, installation_token
from log_processor import trim_log
from metrics import ANALYSIS_ERRORS, RATE_LIMITED

REPO_PATTERN = r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,38}/[a-zA-Z0-9_.-]{1,100}$"
WORK = Lock()
LOG = logging.getLogger(__name__)


class GitHubObject(BaseModel):
    model_config = ConfigDict(strict=True)
    id: PositiveInt


class Repository(GitHubObject):
    full_name: Annotated[str, StringConstraints(pattern=REPO_PATTERN)]


class Run(GitHubObject):
    run_attempt: PositiveInt
    run_number: PositiveInt
    workflow_id: PositiveInt
    head_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    conclusion: str | None


class Delivery(BaseModel):
    action: str
    installation: GitHubObject
    repository: Repository
    workflow_run: Run


@asynccontextmanager
async def lifespan(_: FastAPI):
    await run_in_threadpool(store.initialize)
    recovery = create_task(recover_inbox())
    try:
        yield
    finally:
        recovery.cancel()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), headers={"Content-Type": CONTENT_TYPE_LATEST})


async def read_body(request: Request, limit: int) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > limit:
            raise HTTPException(413, "Payload too large")
        body.extend(chunk)
    return bytes(body)


def allowed(name: str) -> set[str]:
    return {
        value.strip().lower()
        for value in os.getenv(name, "").split(",")
        if value.strip()
    }


def handle_github(delivery: Delivery) -> bool | None:
    run = delivery.workflow_run
    repo = delivery.repository.full_name.lower()
    event = f"github:{run.id}:{run.run_attempt}"
    try:
        with store.connect() as db:
            previous = store.lookup(db, repo, event)
            retry_unavailable = bool(
                previous and (previous["body"] or "").startswith(UNAVAILABLE)
            )
            if previous is not None and previous["done"] and not retry_unavailable:
                return
            token = installation_token(
                os.environ["GITHUB_APP_ID"],
                os.environ["GITHUB_APP_PRIVATE_KEY"].replace("\\n", "\n"),
                delivery.installation.id,
                delivery.repository.id,
            )
            github = GitHubClient(token, repo, os.environ["GITHUB_APP_BOT_LOGIN"])
            pr = github.find_pr(run.head_sha)
            if pr is None:
                return
            failed = run.conclusion == "failure"
            workflow = (repo, pr, run.workflow_id)
            if failed and store.has_newer_success(
                db, workflow, run.run_number, run.run_attempt
            ):
                return
            if not store.reserve(db, repo, event, failure=failed):
                RATE_LIMITED.inc()
                return
            if not failed:
                body = format_fixed(run.head_sha)
            elif (
                previous is not None
                and previous["body"] is not None
                and not retry_unavailable
            ):
                body = previous["body"]
            else:
                try:
                    steps = collect_failure_logs(github, run.id, run.run_attempt)
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    ANALYSIS_ERRORS.inc()
                    result = Analysis(UNAVAILABLE)
                else:
                    result = analyze(steps)
                store.save(db, repo, event, result)
                body = result.body
            if not github.is_current_head(pr, run.head_sha):
                store.finish(db, repo, event)
                return
            if not failed:
                store.record_success(db, workflow, run.run_number, run.run_attempt)
            github.upsert_comment(
                pr,
                run.workflow_id,
                run.run_number,
                run.run_attempt,
                body,
                fixed=not failed,
            )
            store.finish(db, repo, event)
    except Exception as exc:  # noqa: BLE001 - a background job must log without exposing payloads.
        ANALYSIS_ERRORS.inc()
        LOG.error("GitHub delivery %s failed (%s)", event, type(exc).__name__)
        return False


async def process_github(event: str, delivery: Delivery) -> None:
    # Serialize comment creation without holding a worker thread while waiting.
    async with WORK:
        if await run_in_threadpool(handle_github, delivery) is not False:
            await run_in_threadpool(store.acknowledge, event)


async def recover_inbox() -> None:
    for event, payload in await run_in_threadpool(store.pending):
        await process_github(event, Delivery.model_validate_json(payload))


@app.post("/github", status_code=202)
async def github_webhook(request: Request, background: BackgroundTasks):
    secret = os.getenv("GITHUB_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(503, "GitHub webhook is disabled")
    body = await read_body(request, 2_000_000)
    expected = b"sha256=" + hmac.digest(secret.encode(), body, "sha256").hex().encode()
    received = request.headers.get("X-Hub-Signature-256", "").encode()
    if not hmac.compare_digest(expected, received):
        raise HTTPException(401, "Invalid webhook signature")
    if request.headers.get("X-GitHub-Event") != "workflow_run":
        return {"status": "ignored"}
    try:
        delivery = Delivery.model_validate_json(body)
    except ValidationError:
        raise HTTPException(400, "Invalid workflow_run payload") from None
    if str(delivery.installation.id) not in allowed("GITHUB_INSTALLATION_IDS"):
        raise HTTPException(403, "Installation is not allowed")
    if delivery.action != "completed" or delivery.workflow_run.conclusion not in {
        "failure",
        "success",
    }:
        return {"status": "ignored"}
    run = delivery.workflow_run
    event = f"{delivery.repository.id}:{run.id}:{run.run_attempt}"
    await run_in_threadpool(store.enqueue, event, delivery.model_dump_json())
    background.add_task(process_github, event, delivery)
    return {"status": "accepted"}


def handle_jenkins(repo: str, event: str, log: str) -> dict:
    with store.connect() as db:
        previous = store.lookup(db, repo, event)
        if previous is not None and previous["body"] is not None:
            return {"comment": previous["body"], "category": previous["category"]}
        if not store.reserve(db, repo, event, failure=True):
            RATE_LIMITED.inc()
            raise HTTPException(429, "Daily analysis limit reached")
        result = analyze([("Jenkins build", trim_log(log))])
        store.save(db, repo, event, result)
        store.finish(db, repo, event)
        return {"comment": result.body, "category": result.category}


@app.post("/jenkins")
async def jenkins_webhook(request: Request):
    secret = os.getenv("JENKINS_TOKEN")
    if not secret:
        raise HTTPException(503, "Jenkins webhook is disabled")
    token = request.headers.get("Authorization", "").encode()
    if not hmac.compare_digest(token, f"Bearer {secret}".encode()):
        raise HTTPException(401, "Invalid Jenkins token")
    repo = request.headers.get("X-Repository", "").lower()
    build = request.headers.get("X-Build-ID", "")
    if not re.fullmatch(REPO_PATTERN, repo) or not re.fullmatch(
        r"[a-zA-Z0-9_.-]{1,200}", build
    ):
        raise HTTPException(400, "Expected X-Repository and X-Build-ID headers")
    if repo not in allowed("JENKINS_REPOSITORIES"):
        raise HTTPException(403, "Repository is not allowed")
    log = (await read_body(request, 1_000_000)).decode("utf-8", errors="replace")
    if not log.strip():
        raise HTTPException(400, "Expected a build log")
    try:
        async with WORK:
            return await run_in_threadpool(
                handle_jenkins, repo, f"jenkins:{build}", log
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - omit internal details at the HTTP boundary.
        ANALYSIS_ERRORS.inc()
        LOG.error("Jenkins analysis failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Analysis unavailable") from None
