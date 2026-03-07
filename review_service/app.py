"""HTTP service wrapper for the PR review pipeline.

Receives review requests from the landing app, runs the review pipeline,
posts results to GitHub, and calls back to update the agentRun status.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from github_app.github_client import GitHubClient

logger = logging.getLogger(__name__)

app = FastAPI(title="a2a-review Service")

REVIEW_SERVICE_SECRET = os.environ.get("REVIEW_SERVICE_SECRET", "")


class ReviewRequest(BaseModel):
    agent_run_id: str
    installation_id: int
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    personality: str = ""  # Empty = generic code review
    github_token: str
    callback_url: str
    github_username: str = ""  # For attribution in the review comment


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/review")
async def trigger_review(req: ReviewRequest, request: Request):
    """Accept a review request and process it in the background."""
    # Simple auth check
    auth_header = request.headers.get("Authorization", "")
    if REVIEW_SERVICE_SECRET and auth_header != f"Bearer {REVIEW_SERVICE_SECRET}":
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Fire-and-forget background task
    asyncio.create_task(_process_review(req))
    return {"status": "accepted", "agent_run_id": req.agent_run_id}


async def _process_review(req: ReviewRequest):
    """Clone repo, run review pipeline, post to GitHub, callback."""
    client = GitHubClient(req.github_token)
    clone_path = None

    try:
        # 1. Fetch diff
        diff = await client.fetch_pr_diff(req.owner, req.repo, req.pr_number)
        if not diff.strip():
            await _callback(req.callback_url, req.agent_run_id, "completed")
            return

        # 2. Clone repo
        clone_dir = tempfile.mkdtemp(prefix="a2a-review-")
        clone_path = await client.clone_repo(
            req.owner, req.repo, req.pr_number, req.head_sha, clone_dir
        )

        # 3. Run review with personality
        from pr_review_agent.config import Config as ReviewConfig
        from pr_review_agent.review import review_diff

        review_config = ReviewConfig(
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            morph_api_key=os.environ.get("MORPH_API_KEY", ""),
            skip_dir_creation=True,
            personality=req.personality,
        )

        comments = await asyncio.to_thread(
            review_diff,
            diff,
            repo_path=str(clone_path),
            config=review_config,
            personality=req.personality,
        )

        # 4. Post review to GitHub
        if comments:
            summary_line = f"Found {len(comments)} issue{'s' if len(comments) != 1 else ''}"
            if req.personality and req.github_username:
                review_body = (
                    f"@{req.github_username}'s review twin\n\n"
                    f"{summary_line}\n\n---\n"
                    f"*a2a-review based on @{req.github_username}'s coding preferences*"
                )
            else:
                review_body = (
                    f"## Morph Code Review\n\n"
                    f"{summary_line}"
                )

            await client.post_review(
                req.owner, req.repo, req.pr_number, req.head_sha,
                comments, diff, review_body,
            )

        # 5. Callback to landing
        await _callback(req.callback_url, req.agent_run_id, "completed")

        logger.info(
            "Completed review for %s/%s PR #%d: %d comments",
            req.owner, req.repo, req.pr_number, len(comments) if comments else 0,
        )

    except Exception:
        logger.exception(
            "Review failed for %s/%s PR #%d", req.owner, req.repo, req.pr_number
        )
        await _callback(req.callback_url, req.agent_run_id, "failed")

    finally:
        if clone_path:
            GitHubClient.cleanup_clone(clone_path)
        await client.close()


async def _callback(url: str, agent_run_id: str, status: str):
    """Notify the landing app of the review result."""
    try:
        async with httpx.AsyncClient() as http:
            await http.post(
                url,
                json={"agent_run_id": agent_run_id, "status": status},
                headers={"Authorization": f"Bearer {REVIEW_SERVICE_SECRET}"},
                timeout=10,
            )
    except Exception:
        logger.exception("Failed to callback to %s", url)
