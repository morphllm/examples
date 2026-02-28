"""GitHub API client for fetching diffs, posting reviews, and managing check runs."""

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"


class GitHubClient:
    """Async client for GitHub API operations scoped to an installation token."""

    def __init__(self, token: str):
        self.token = token
        self._client = httpx.AsyncClient(
            base_url=GITHUB_API,
            headers={
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=60,
        )

    async def close(self):
        await self._client.aclose()

    async def fetch_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Fetch the unified diff for a pull request."""
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )
        resp.raise_for_status()
        return resp.text

    async def clone_repo(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        head_sha: str,
        base_dir: str,
    ) -> Path:
        """Shallow clone the repo and checkout the PR head."""
        dest = Path(base_dir) / f"{owner}_{repo}_pr{pr_number}"
        if dest.exists():
            shutil.rmtree(dest)

        clone_url = f"https://x-access-token:{self.token}@github.com/{owner}/{repo}.git"

        # Shallow clone
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", clone_url, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed: {stderr.decode()}")

        # Fetch PR head
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(dest), "fetch", "origin",
            f"pull/{pr_number}/head:review",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git fetch PR head failed: {stderr.decode()}")

        # Checkout PR branch
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(dest), "checkout", "review",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

        logger.info("Cloned %s/%s PR #%d to %s", owner, repo, pr_number, dest)
        return dest

    async def create_check_run(
        self, owner: str, repo: str, head_sha: str
    ) -> int:
        """Create a check run in 'in_progress' state. Returns check_run_id."""
        resp = await self._client.post(
            f"/repos/{owner}/{repo}/check-runs",
            json={
                "name": "PR Review Agent",
                "head_sha": head_sha,
                "status": "in_progress",
                "output": {
                    "title": "Reviewing PR...",
                    "summary": "AI code review is in progress.",
                },
            },
        )
        resp.raise_for_status()
        return resp.json()["id"]

    async def complete_check_run(
        self,
        owner: str,
        repo: str,
        check_run_id: int,
        conclusion: str,
        title: str,
        summary: str,
    ):
        """Complete a check run with a conclusion."""
        resp = await self._client.patch(
            f"/repos/{owner}/{repo}/check-runs/{check_run_id}",
            json={
                "status": "completed",
                "conclusion": conclusion,
                "output": {"title": title, "summary": summary},
            },
        )
        resp.raise_for_status()

    async def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        comments: list,
        summary: str,
    ):
        """Post a PR review with inline comments."""
        review_comments = []
        for c in comments:
            review_comments.append({
                "path": c.file_path,
                "line": c.line_number,
                "side": "RIGHT",
                "body": (
                    f"**{c.severity}** | `{c.category}` | "
                    f"confidence: {c.confidence:.0%}\n\n{c.body}"
                ),
            })

        resp = await self._client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            json={
                "event": "COMMENT",
                "body": summary,
                "comments": review_comments,
            },
        )
        resp.raise_for_status()
        logger.info(
            "Posted review on %s/%s PR #%d with %d comments",
            owner, repo, pr_number, len(review_comments),
        )

    @staticmethod
    def cleanup_clone(path: Path):
        """Remove a cloned repo directory."""
        if path and path.exists():
            shutil.rmtree(path, ignore_errors=True)
            logger.info("Cleaned up clone at %s", path)
