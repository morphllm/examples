"""Background worker for processing PR review jobs."""

from __future__ import annotations

import asyncio
import logging
import time

from github_app.auth import get_installation_token
from github_app.config import AppConfig
from github_app.github_client import GitHubClient
from github_app.telemetry import make_event_emitter, send_review_event

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

        t_start = time.monotonic()
        metrics = {}
        base_ctx = {
            "service": "morph-ghapp", "source": "webhook",
            "repo": full_name, "pr_number": pr_number, "head_sha": head_sha,
        }
        on_event = make_event_emitter(base_ctx)

        try:
            check_run_id = await client.create_check_run(owner, repo, head_sha)

            # Fetch diff
            diff = await client.fetch_pr_diff(owner, repo, pr_number)
            if not diff.strip():
                await client.complete_check_run(
                    owner, repo, check_run_id,
                    "success", "No changes to review", "The PR diff is empty.",
                )
                on_event("review.completed", {
                    "status": "empty_diff",
                    "duration_total_s": round(time.monotonic() - t_start, 1),
                })
                return

            on_event("review.started", {
                "diff_size_chars": len(diff),
            })

            # Clone for agentic tool use
            t_clone = time.monotonic()
            clone_path = await client.clone_repo(
                owner, repo, pr_number, head_sha, app_config.clone_base_dir
            )
            duration_clone = round(time.monotonic() - t_clone, 1)
            on_event("review.clone", {
                "duration_s": duration_clone,
                "clone_path": str(clone_path),
                "success": True,
            })

            # Build pr_review_agent config
            from pr_review_agent.config import Config as ReviewConfig
            from pr_review_agent.review import review_diff

            review_config = ReviewConfig(
                anthropic_api_key=app_config.anthropic_api_key,
                morph_api_key=app_config.morph_api_key,
                skip_dir_creation=True,
            )

            # Run review (sync call, runs in thread to avoid blocking event loop)
            t_review = time.monotonic()
            comments = await asyncio.to_thread(
                review_diff,
                diff,
                repo_path=str(clone_path),
                config=review_config,
                organism_path=app_config.organism_path or None,
                max_issues=app_config.max_issues_per_pr,
                metrics_out=metrics,
                on_event=on_event,
            )
            duration_review = round(time.monotonic() - t_review, 1)

            t_post = time.monotonic()
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
            duration_post = round(time.monotonic() - t_post, 1)
            on_event("review.post", {
                "comments_posted": len(comments) if comments else 0,
                "duration_s": duration_post,
                "success": True,
            })

            logger.info(
                "Completed review for %s PR #%d: %d comments",
                full_name, pr_number, len(comments),
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

        except Exception as exc:
            logger.exception("Review failed for %s PR #%d", full_name, pr_number)
            on_event("review.failed", {
                "duration_total_s": round(time.monotonic() - t_start, 1),
                "status": "failure",
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
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
