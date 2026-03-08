"""FastAPI server for the PR Review Agent GitHub App."""

import asyncio
import hashlib
import hmac
import json
import logging
import os
from collections import OrderedDict
from functools import lru_cache

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request
from pydantic import BaseModel

from github_app.config import AppConfig
from github_app.github_client import GitHubClient
from github_app.worker import process_review

logger = logging.getLogger(__name__)

app = FastAPI(title="PR Review Agent")

# Delivery dedup (bounded LRU)
_seen_deliveries: OrderedDict[str, None] = OrderedDict()
_MAX_SEEN = 1000


@lru_cache
def _get_config() -> AppConfig:
    """Lazy-load config on first request, not at import time."""
    cfg = AppConfig()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return cfg


def _verify_signature(body: bytes, secret: str, signature_header: str) -> bool:
    """Verify X-Hub-Signature-256 from GitHub webhook."""
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.HMAC(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):
    config = _get_config()
    body = await request.body()

    # Verify signature
    signature = request.headers.get("X-Hub-Signature-256", "")
    if not _verify_signature(body, config.github_webhook_secret, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    event = request.headers.get("X-GitHub-Event", "")
    delivery_id = request.headers.get("X-GitHub-Delivery", "")

    # Dedupe
    if delivery_id in _seen_deliveries:
        logger.debug("Duplicate delivery %s, skipping", delivery_id)
        return {"status": "duplicate"}
    _seen_deliveries[delivery_id] = None
    if len(_seen_deliveries) > _MAX_SEEN:
        _seen_deliveries.popitem(last=False)

    payload = json.loads(body)

    if event == "pull_request" and payload.get("action") in (
        "opened",
        "synchronize",
        "reopened",
    ):
        pr = payload["pull_request"]
        logger.info(
            "Enqueuing review for %s PR #%d (action=%s)",
            pr["base"]["repo"]["full_name"],
            pr["number"],
            payload["action"],
        )
        background_tasks.add_task(process_review, payload, config)

    return {"status": "ok"}


class ReviewRequest(BaseModel):
    """Request from the landing app to review a PR."""
    agent_run_id: str = ""
    installation_id: int = 0
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    personality: str = ""
    github_token: str
    callback_url: str = ""
    github_username: str = ""
    # Multi-provider support
    provider: str = "anthropic"  # "anthropic" | "openai" | "google"
    model: str = ""  # Override model name (e.g. "gpt-5.4", "gemini-3.1-pro-preview")
    openai_api_key: str = ""  # Override OpenAI API key (optional, falls back to env)
    google_api_key: str = ""  # Override Google API key (optional, falls back to env)


REVIEW_API_SECRET = os.environ.get("REVIEW_API_SECRET", "") or os.environ.get("GHAPP_INTERNAL_SECRET", "")


async def _run_review_from_api(req: ReviewRequest):
    """Run the review pipeline from an API request (not a webhook)."""
    import time
    import uuid
    from github_app.telemetry import make_event_emitter

    config = _get_config()
    client = GitHubClient(req.github_token)
    clone_path = None
    full_name = f"{req.owner}/{req.repo}"
    agent_run_id = req.agent_run_id or str(uuid.uuid4())
    t_start = time.monotonic()
    metrics = {}
    base_ctx = {
        "service": "morph-ghapp", "source": "api",
        "repo": full_name, "pr_number": req.pr_number,
        "head_sha": req.head_sha, "agent_run_id": agent_run_id,
        "personality": req.personality or "",
    }
    on_event = make_event_emitter(base_ctx)

    try:
        diff = await client.fetch_pr_diff(req.owner, req.repo, req.pr_number)
        if not diff.strip():
            logger.info("Empty diff for %s PR #%d, skipping", full_name, req.pr_number)
            on_event("review.completed", {
                "status": "empty_diff",
                "duration_total_s": round(time.monotonic() - t_start, 1),
            })
            if req.callback_url:
                await _callback(req.callback_url, agent_run_id, "completed")
            return

        on_event("review.started", {
            "diff_size_chars": len(diff),
        })

        t_clone = time.monotonic()
        clone_path = await client.clone_repo(
            req.owner, req.repo, req.pr_number, req.head_sha, config.clone_base_dir
        )
        duration_clone = round(time.monotonic() - t_clone, 1)
        on_event("review.clone", {
            "duration_s": duration_clone,
            "clone_path": str(clone_path),
            "success": True,
        })

        from pr_review_agent.config import Config as ReviewConfig
        from pr_review_agent.review import review_diff

        review_config = ReviewConfig(
            provider=req.provider,
            anthropic_api_key=config.anthropic_api_key,
            morph_api_key=config.morph_api_key,
            openai_api_key=req.openai_api_key or config.openai_api_key,
            google_api_key=req.google_api_key or config.google_api_key,
            skip_dir_creation=True,
            personality=req.personality if req.personality else None,
        )
        if req.model:
            review_config.model = req.model

        t_review = time.monotonic()
        comments = await asyncio.to_thread(
            review_diff,
            diff,
            repo_path=str(clone_path),
            config=review_config,
            organism_path=config.organism_path or None,
            max_issues=config.max_issues_per_pr,
            personality=req.personality if req.personality else None,
            metrics_out=metrics,
            on_event=on_event,
        )
        duration_review = round(time.monotonic() - t_review, 1)

        t_post = time.monotonic()
        if comments:
            summary_line = f"Found {len(comments)} issue{'s' if len(comments) != 1 else ''}"
            if req.personality and req.github_username:
                review_body = (
                    f"@{req.github_username}'s review twin\n\n"
                    f"{summary_line}\n\n---\n"
                    f"*a2a-review based on @{req.github_username}'s coding preferences*"
                )
            else:
                review_body = f"## Morph Code Review\n\n{summary_line}"

            await client.post_review(
                req.owner, req.repo, req.pr_number, req.head_sha,
                comments, diff, review_body,
            )
        duration_post = round(time.monotonic() - t_post, 1)
        on_event("review.post", {
            "comments_posted": len(comments) if comments else 0,
            "duration_s": duration_post,
            "success": True,
        })

        logger.info(
            "API review completed for %s PR #%d: %d comments",
            full_name, req.pr_number, len(comments) if comments else 0,
        )

        on_event("review.completed", {
            "duration_total_s": round(time.monotonic() - t_start, 1),
            "duration_clone_s": duration_clone,
            "duration_review_s": duration_review,
            "duration_post_s": duration_post,
            "issues_found": len(comments) if comments else 0,
            "status": "success",
            "diff_files": metrics.get("diff_files", 0),
            "diff_size_chars": len(diff),
            "tool_rounds": metrics.get("tool_rounds", 0),
            "api_calls": metrics.get("api_calls", 0),
            "api_calls_review": metrics.get("api_calls_review", 0),
            "api_calls_extract": metrics.get("api_calls_extract", 0),
            "total_input_tokens": metrics.get("total_input_tokens", 0),
            "total_output_tokens": metrics.get("total_output_tokens", 0),
            **{f"tool_{k}": v for k, v in metrics.get("tool_counts", {}).items()},
        })

        if req.callback_url:
            await _callback(req.callback_url, agent_run_id, "completed")

    except Exception as exc:
        logger.exception("API review failed for %s PR #%d", full_name, req.pr_number)
        on_event("review.failed", {
            "duration_total_s": round(time.monotonic() - t_start, 1),
            "status": "failure",
            "error": str(exc),
            "error_type": type(exc).__name__,
        })
        if req.callback_url:
            await _callback(req.callback_url, agent_run_id, "failed")
    finally:
        if clone_path:
            GitHubClient.cleanup_clone(clone_path)
        await client.close()


async def _callback(url: str, agent_run_id: str, status: str):
    """Notify the landing app of the review result."""
    try:
        import httpx
        async with httpx.AsyncClient() as http:
            await http.post(
                url,
                json={"agent_run_id": agent_run_id, "status": status},
                headers={"Authorization": f"Bearer {REVIEW_API_SECRET}"},
                timeout=10,
            )
    except Exception:
        logger.exception("Callback failed to %s", url)


@app.post("/review")
async def review_api(req: ReviewRequest, request: Request):
    """Accept a review request from the landing app (or any caller with the secret)."""
    auth = request.headers.get("Authorization", "")
    if REVIEW_API_SECRET and auth != f"Bearer {REVIEW_API_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    asyncio.create_task(_run_review_from_api(req))
    return {"status": "accepted", "agent_run_id": req.agent_run_id or "generated-server-side"}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.1.0"}
