"""Background worker for processing PR review jobs."""

from __future__ import annotations

import asyncio
import logging

from github_app.auth import get_installation_token
from github_app.config import AppConfig
from github_app.github_client import GitHubClient

logger = logging.getLogger(__name__)

_semaphore: asyncio.Semaphore | None = None


def _get_semaphore(max_concurrent: int) -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(max_concurrent)
    return _semaphore


async def process_review(payload: dict, app_config: AppConfig):
    """Process a PR review job in the background."""
    sem = _get_semaphore(app_config.max_concurrent_reviews)
    async with sem:
        installation_id = payload["installation"]["id"]
        pr = payload["pull_request"]
        full_name = pr["base"]["repo"]["full_name"]
        owner, repo = full_name.split("/")
        pr_number = pr["number"]
        head_sha = pr["head"]["sha"]

        logger.info("Starting review for %s PR #%d (%s)", full_name, pr_number, head_sha[:8])

        token = get_installation_token(
            app_config.github_app_id, installation_id, app_config.github_private_key
        )
        client = GitHubClient(token)
        clone_path = None
        check_run_id = None

        try:
            check_run_id = await client.create_check_run(owner, repo, head_sha)

            # Fetch diff
            diff = await client.fetch_pr_diff(owner, repo, pr_number)
            if not diff.strip():
                await client.complete_check_run(
                    owner, repo, check_run_id,
                    "success", "No changes to review", "The PR diff is empty.",
                )
                return

            # Clone for agentic tool use
            clone_path = await client.clone_repo(
                owner, repo, pr_number, head_sha, app_config.clone_base_dir
            )

            # Build pr_review_agent config
            from pr_review_agent.config import Config as ReviewConfig
            from pr_review_agent.review import review_diff

            review_config = ReviewConfig(
                anthropic_api_key=app_config.anthropic_api_key,
                morph_api_key=app_config.morph_api_key,
                skip_dir_creation=True,
            )

            # Run review (sync call, runs in thread to avoid blocking event loop)
            comments = await asyncio.to_thread(
                review_diff,
                diff,
                repo_path=str(clone_path),
                config=review_config,
                max_issues=app_config.max_issues_per_pr,
            )

            if comments:
                summary = f"Found {len(comments)} issue{'s' if len(comments) != 1 else ''}"
                await client.post_review(
                    owner, repo, pr_number, head_sha, comments, diff, summary,
                )
                await client.complete_check_run(
                    owner, repo, check_run_id,
                    "action_required",
                    summary,
                    f"PR Review Agent found {len(comments)} potential issue{'s' if len(comments) != 1 else ''} in this PR.",
                )
            else:
                await client.complete_check_run(
                    owner, repo, check_run_id,
                    "success",
                    "No issues found",
                    "PR Review Agent found no issues in this PR.",
                )

            logger.info(
                "Completed review for %s PR #%d: %d comments",
                full_name, pr_number, len(comments),
            )

        except Exception:
            logger.exception("Review failed for %s PR #%d", full_name, pr_number)
            if check_run_id:
                try:
                    await client.complete_check_run(
                        owner, repo, check_run_id,
                        "neutral",
                        "Review failed",
                        "An error occurred during the review. Check server logs for details.",
                    )
                except Exception:
                    logger.exception("Failed to update check run")
        finally:
            if clone_path:
                GitHubClient.cleanup_clone(clone_path)
            await client.close()
