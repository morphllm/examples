"""Generate candidates.json for the benchmark pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from pr_review_agent.pipeline.reviewer import ReviewIssue


def format_candidates(
    pr_url: str,
    issues: list[ReviewIssue],
    tool_name: str = "pr_review_agent",
) -> dict:
    """Format review issues as benchmark candidates for a single PR.

    The benchmark expects candidates in this format per (pr_url, tool):
    [
        {
            "text": "issue description",
            "path": "file/path.py" or null,
            "line": 42 or null,
            "source": "extracted"
        }
    ]

    Args:
        pr_url: The golden PR URL (key in benchmark data).
        issues: Review issues from the pipeline.
        tool_name: Name of our tool for the benchmark.

    Returns:
        Dict mapping tool_name to list of candidate dicts.
    """
    candidates = []
    for issue in issues:
        candidates.append({
            "text": issue.comment,
            "path": issue.file_path or None,
            "line": issue.line_number if issue.line_number > 0 else None,
            "source": "extracted",
        })
    return {tool_name: candidates}


def write_candidates_json(
    all_candidates: dict[str, dict],
    output_path: Path,
) -> None:
    """Write the full candidates.json file.

    Format matches the benchmark's expected structure:
    {
        "https://github.com/.../pull/123": {
            "pr_review_agent": [
                {"text": "...", "path": "...", "line": N, "source": "extracted"}
            ]
        }
    }

    Args:
        all_candidates: Dict mapping PR URL -> tool -> candidates list.
        output_path: Path to write candidates.json.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(all_candidates, f, indent=2)
    print(f"Wrote candidates to {output_path}")


def write_review_details(
    pr_url: str,
    issues: list[ReviewIssue],
    output_dir: Path,
) -> None:
    """Write detailed review results for debugging.

    Args:
        pr_url: PR URL being reviewed.
        issues: All issues found (before filtering).
        output_dir: Directory to write detail files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Create safe filename from URL
    safe_name = pr_url.split("/")[-1]
    repo_name = pr_url.split("/")[-3] if "/" in pr_url else "unknown"
    filename = f"{repo_name}_pr{safe_name}_details.json"

    details = {
        "pr_url": pr_url,
        "total_issues": len(issues),
        "issues": [
            {
                **issue.to_dict(),
                "source_pass": issue.source_pass,
            }
            for issue in issues
        ],
    }

    with open(output_dir / filename, "w") as f:
        json.dump(details, f, indent=2)


def merge_with_existing_candidates(
    existing_path: Path,
    new_candidates: dict[str, dict],
    tool_name: str = "pr_review_agent",
) -> dict:
    """Merge new candidates with existing candidates.json.

    Args:
        existing_path: Path to existing candidates.json.
        new_candidates: New candidates to merge in.
        tool_name: Our tool name.

    Returns:
        Merged candidates dict.
    """
    if existing_path.exists():
        with open(existing_path) as f:
            merged = json.load(f)
    else:
        merged = {}

    for pr_url, tool_candidates in new_candidates.items():
        if pr_url not in merged:
            merged[pr_url] = {}
        merged[pr_url][tool_name] = tool_candidates.get(tool_name, [])

    return merged
