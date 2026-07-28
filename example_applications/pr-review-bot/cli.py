#!/usr/bin/env python3
"""Review a pull request from the command line, without running the webhook server.

Usage:
    python cli.py https://github.com/owner/repo/pull/123
    python cli.py owner/repo#123 --max-issues 5 --no-clone

Needs MORPH_API_KEY plus a key for the reviewer provider (see .env.example).
A GITHUB_TOKEN is used if present, and is required for private repos.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

from github_client import GitHubClient
from review_engine.config import Config
from review_engine.review import review_diff

PR_PATTERNS = (
    r"^https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)",
    r"^([^/]+)/([^#]+)#(\d+)$",
)


def parse_pr_ref(ref: str) -> tuple[str, str, int]:
    """Parse a PR URL or owner/repo#number shorthand."""
    for pattern in PR_PATTERNS:
        match = re.match(pattern, ref)
        if match:
            owner, repo, number = match.groups()
            return owner, repo, int(number)
    raise ValueError(
        f"Could not parse {ref!r}. "
        "Expected https://github.com/owner/repo/pull/123 or owner/repo#123."
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pr", help="PR URL or owner/repo#number")
    parser.add_argument("--max-issues", type=int, default=8)
    parser.add_argument(
        "--no-clone",
        action="store_true",
        help="Review the diff alone, without cloning. Disables WarpGrep context search.",
    )
    args = parser.parse_args()

    owner, repo, pr_number = parse_pr_ref(args.pr)
    client = GitHubClient(os.environ.get("GITHUB_TOKEN", ""))
    clone_path = None

    try:
        diff = await client.fetch_pr_diff(owner, repo, pr_number)
        if not diff.strip():
            print(f"{owner}/{repo}#{pr_number} has an empty diff.")
            return 0

        if not args.no_clone:
            print(f"Cloning {owner}/{repo}...", file=sys.stderr)
            clone_path = await client.clone_repo(owner, repo, pr_number, "/tmp/pr-review-clones")

        print(f"Reviewing {owner}/{repo}#{pr_number} ({len(diff)} chars of diff)...", file=sys.stderr)
        comments = await asyncio.to_thread(
            review_diff,
            diff,
            repo_path=str(clone_path) if clone_path else None,
            config=Config(),
            max_issues=args.max_issues,
        )

        if not comments:
            print("No issues found.")
            return 0

        for comment in comments:
            print(f"\n{comment.file_path}:{comment.line_number}")
            print(f"  [{comment.severity}] {comment.category} (confidence {comment.confidence:.2f})")
            for line in comment.body.splitlines():
                print(f"  {line}")
        return 0
    finally:
        if clone_path:
            GitHubClient.cleanup_clone(clone_path)
        await client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
