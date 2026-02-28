"""GitHub API client for fetching diffs, posting reviews, and managing check runs."""

import asyncio
import logging
import shutil
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
            timeout=httpx.Timeout(120, connect=30),
        )

    async def close(self):
        await self._client.aclose()

    async def fetch_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Fetch the unified diff for a pull request."""
        resp = await self._client.get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
            timeout=180,  # large diffs can be slow
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

        # Shallow clone default branch
        proc = await asyncio.create_subprocess_exec(
            "git", "clone", "--depth=1", clone_url, str(dest),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed: {stderr.decode()}")

        # Fetch PR head (shallow)
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", str(dest), "fetch", "--depth=1", "origin",
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
        commit_id: str,
        comments: list,
        diff: str,
        summary: str,
    ):
        """Post a PR review with inline comments.

        Comments with invalid line numbers (not in the diff) are filtered out
        to prevent GitHub from rejecting the entire review with a 422 error.
        """
        # Build a set of valid (file_path, line_number) from the diff
        valid_lines = _extract_valid_diff_lines(diff)

        review_comments = []
        skipped = []
        for c in comments:
            # Skip comments with invalid line numbers
            if c.line_number <= 0:
                skipped.append(c)
                continue
            if (c.file_path, c.line_number) not in valid_lines:
                skipped.append(c)
                continue

            review_comments.append({
                "path": c.file_path,
                "line": c.line_number,
                "side": "RIGHT",
                "body": (
                    f"**{c.severity}** | `{c.category}` | "
                    f"confidence: {c.confidence:.0%}\n\n{c.body}"
                ),
            })

        if skipped:
            logger.warning(
                "Skipped %d comments with invalid line numbers for %s/%s PR #%d",
                len(skipped), owner, repo, pr_number,
            )

        if not review_comments:
            # All comments had invalid lines; post as a plain PR comment instead
            body = summary + "\n\n"
            for c in comments:
                body += (
                    f"**{c.severity}** | `{c.category}` | "
                    f"`{c.file_path}:{c.line_number}` | "
                    f"confidence: {c.confidence:.0%}\n\n{c.body}\n\n---\n\n"
                )
            resp = await self._client.post(
                f"/repos/{owner}/{repo}/issues/{pr_number}/comments",
                json={"body": body},
            )
            resp.raise_for_status()
            logger.info(
                "Posted fallback comment on %s/%s PR #%d (%d comments as text)",
                owner, repo, pr_number, len(comments),
            )
            return

        resp = await self._client.post(
            f"/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            json={
                "commit_id": commit_id,
                "event": "COMMENT",
                "body": summary,
                "comments": review_comments,
            },
        )
        resp.raise_for_status()
        logger.info(
            "Posted review on %s/%s PR #%d with %d inline comments (%d skipped)",
            owner, repo, pr_number, len(review_comments), len(skipped),
        )

    @staticmethod
    def cleanup_clone(path: Path):
        """Remove a cloned repo directory."""
        if path and path.exists():
            shutil.rmtree(path, ignore_errors=True)
            logger.info("Cleaned up clone at %s", path)


def _extract_valid_diff_lines(diff: str) -> set[tuple[str, int]]:
    """Parse a unified diff to extract valid (file_path, new_line_number) pairs.

    These are the line numbers that GitHub will accept for inline comments
    on the RIGHT side of the diff.
    """
    valid = set()
    current_file = None
    current_line = 0

    for line in diff.split("\n"):
        if line.startswith("diff --git"):
            parts = line.split(" ")
            if len(parts) > 3:
                current_file = parts[3][2:]  # strip "b/"
        elif line.startswith("@@") and current_file:
            # Parse @@ -old,count +new,count @@
            import re
            m = re.search(r"\+(\d+)", line)
            if m:
                current_line = int(m.group(1))
        elif current_file and current_line > 0:
            if line.startswith("+") and not line.startswith("+++"):
                valid.add((current_file, current_line))
                current_line += 1
            elif line.startswith("-") and not line.startswith("---"):
                pass  # deleted lines don't increment new line counter
            elif not line.startswith("\\"):
                # Context line: also valid for comments
                valid.add((current_file, current_line))
                current_line += 1

    return valid
