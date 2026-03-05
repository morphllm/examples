"""Clean interface for PR review - usable from benchmark, GitHub App, or CLI.

This is the public API. Everything else is implementation detail.

Usage:
    from pr_review_agent.review import review_diff, ReviewComment

    # From a GitHub webhook:
    comments = review_diff(diff_text, repo_path="/path/to/clone")

    # With evolved prompts:
    comments = review_diff(diff_text, repo_path="/path/to/clone",
                           organism_path="evolver/output/best_organism.json")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pr_review_agent.config import Config
from pr_review_agent.pipeline.confidence_filter import ConfidenceFilter
from pr_review_agent.pipeline.diff_parser import filter_reviewable_files, parse_diff
from pr_review_agent.pipeline.reviewer import Reviewer


@dataclass
class ReviewComment:
    """A single review comment to post on a PR."""
    file_path: str
    line_number: int
    body: str
    severity: str  # Critical, High, Medium, Low
    category: str  # logic_error, api_misuse, race_condition, etc.
    confidence: float  # 0.0 - 1.0


def review_diff(
    diff: str,
    *,
    repo_path: str | None = None,
    organism_path: str | None = None,
    max_issues: int = 8,
    config: Config | None = None,
    personality: str | None = None,
) -> list[ReviewComment]:
    """Review a unified diff and return comments.

    Args:
        diff: Unified diff text (as from `git diff` or GitHub API)
        repo_path: Path to local repo clone (enables WarpGrep context search)
        organism_path: Path to evolved organism JSON (optional, uses defaults if None)
        max_issues: Maximum comments to return per review
        config: Config override (optional, creates default if None)
        personality: Optional reviewer personality text for persona injection

    Returns:
        List of ReviewComment, sorted by confidence descending
    """
    if config is None:
        config = Config()
    if personality:
        config.personality = personality

    # Parse diff
    file_diffs = filter_reviewable_files(parse_diff(diff))
    if not file_diffs:
        return []

    # Set up reviewer
    reviewer = Reviewer(config)
    confidence_filter = ConfidenceFilter(config)

    if organism_path:
        from pr_review_agent.evolver.organism import CodeReviewOrganism
        import json
        with open(organism_path) as f:
            data = json.load(f)
        organism = CodeReviewOrganism(**data)
        reviewer.configure_from_organism(organism)
        confidence_filter = ConfidenceFilter(
            config, base_threshold_override=organism.confidence_threshold
        )
        max_issues = organism.max_issues_per_pr

    # Run review pipeline (includes LLM aggregation — no separate judge needed)
    issues = reviewer.review_pr(file_diffs, repo_path=repo_path)

    # Filter
    filtered = confidence_filter.filter(issues)

    # Cap and sort
    filtered.sort(key=lambda x: x.confidence, reverse=True)
    filtered = filtered[:max_issues]

    # Convert to public interface
    return [
        ReviewComment(
            file_path=issue.file_path,
            line_number=issue.line_number,
            body=issue.comment,
            severity=issue.severity,
            category=issue.category,
            confidence=issue.confidence,
        )
        for issue in filtered
    ]
