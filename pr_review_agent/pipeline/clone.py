"""Clone benchmark fork repos and checkout PRs."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from pr_review_agent.config import Config


def load_benchmark_data(config: Config) -> dict:
    """Load the benchmark_data.json file."""
    data_file = config.benchmark_dir / "results" / "benchmark_data.json"
    if not data_file.exists():
        raise FileNotFoundError(f"Benchmark data not found: {data_file}")
    with open(data_file) as f:
        return json.load(f)


def load_golden_comments(config: Config) -> dict[str, list[dict]]:
    """Load all golden comments, keyed by PR URL."""
    golden_dir = config.benchmark_dir / "golden_comments"
    all_comments = {}
    for json_file in golden_dir.glob("*.json"):
        with open(json_file) as f:
            prs = json.load(f)
        for pr in prs:
            url = pr["url"]
            all_comments[url] = pr["comments"]
    return all_comments


def clone_repo(repo_url: str, dest: Path, timeout: int = 120) -> Path:
    """Clone a git repository if not already cloned.

    Args:
        repo_url: GitHub repository URL.
        dest: Directory to clone into.
        timeout: Git clone timeout in seconds.

    Returns:
        Path to the cloned repository.
    """
    # Extract org/repo from URL
    parts = repo_url.rstrip("/").split("/")
    repo_name = f"{parts[-2]}__{parts[-1]}"
    repo_path = dest / repo_name

    if repo_path.exists():
        return repo_path

    subprocess.run(
        ["git", "clone", "--depth", "1", repo_url, str(repo_path)],
        check=True,
        capture_output=True,
        timeout=timeout,
    )
    return repo_path


def checkout_pr(repo_path: Path, pr_number: int, timeout: int = 60) -> bool:
    """Fetch and checkout a PR branch.

    Args:
        repo_path: Path to the git repository.
        pr_number: PR number to checkout.
        timeout: Git fetch timeout in seconds.

    Returns:
        True if checkout succeeded.
    """
    try:
        subprocess.run(
            ["git", "fetch", "origin", f"pull/{pr_number}/head:pr-{pr_number}"],
            cwd=str(repo_path),
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        subprocess.run(
            ["git", "checkout", f"pr-{pr_number}"],
            cwd=str(repo_path),
            check=True,
            capture_output=True,
            timeout=timeout,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False


def get_pr_diff(repo_path: Path, pr_number: int, timeout: int = 30) -> str | None:
    """Get the diff for a PR.

    Tries to get the diff between the PR branch and main/master.

    Args:
        repo_path: Path to the git repository.
        pr_number: PR number.
        timeout: Git diff timeout in seconds.

    Returns:
        Diff string or None if failed.
    """
    for base in ("main", "master"):
        try:
            result = subprocess.run(
                ["git", "diff", f"{base}...pr-{pr_number}"],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    return None


def extract_pr_number(url: str) -> int | None:
    """Extract PR number from a GitHub URL."""
    parts = url.rstrip("/").split("/")
    try:
        pull_idx = parts.index("pull")
        return int(parts[pull_idx + 1])
    except (ValueError, IndexError):
        return None


def fetch_pr_diff_from_github(pr_url: str, timeout: int = 30) -> str | None:
    """Fetch PR diff from GitHub API using the gh CLI.

    Args:
        pr_url: Full GitHub PR URL.
        timeout: Command timeout in seconds.

    Returns:
        Diff string or None if failed.
    """
    # Parse owner/repo/number from URL
    parts = pr_url.rstrip("/").split("/")
    try:
        pull_idx = parts.index("pull")
        owner = parts[pull_idx - 2]
        repo = parts[pull_idx - 1]
        pr_number = parts[pull_idx + 1]
    except (ValueError, IndexError):
        return None

    try:
        result = subprocess.run(
            ["gh", "api", f"/repos/{owner}/{repo}/pulls/{pr_number}",
             "-H", "Accept: application/vnd.github.v3.diff"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0 and result.stdout:
            return result.stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def reconstruct_diff_from_reviews(entry: dict) -> str | None:
    """Reconstruct a pseudo-diff from benchmark review comments.

    When the actual diff is not available (no clone, no GitHub API),
    build a minimal diff from the paths and line numbers referenced
    in other tools' review comments. This gives the reviewer enough
    context to identify file structure.

    Args:
        entry: Benchmark data entry for a single PR.

    Returns:
        Reconstructed diff string, or None if no useful data.
    """
    # Collect all unique file paths from all reviews
    file_data: dict[str, list[dict]] = {}
    for review in entry.get("reviews", []):
        for comment in review.get("review_comments", []):
            path = comment.get("path")
            if path:
                file_data.setdefault(path, []).append(comment)

    if not file_data:
        return None

    # Build diff from collected data
    diff_parts = []
    for path, comments in file_data.items():
        diff_parts.append(f"diff --git a/{path} b/{path}")
        diff_parts.append(f"--- a/{path}")
        diff_parts.append(f"+++ b/{path}")

        # Sort comments by line number
        sorted_comments = sorted(
            comments,
            key=lambda c: c.get("line") or c.get("original_line") or 0,
        )

        seen_lines = set()
        for c in sorted_comments:
            line = c.get("line") or c.get("original_line") or 1
            if line in seen_lines:
                continue
            seen_lines.add(line)

            body = c.get("body", "")
            # Truncate long bodies but keep enough for context
            if len(body) > 500:
                body = body[:500] + "..."

            diff_parts.append(f"@@ -{line},10 +{line},10 @@")
            diff_parts.append(f" // Context around line {line}")
            diff_parts.append(f"+// Changed code near line {line}")

    return "\n".join(diff_parts) if diff_parts else None
