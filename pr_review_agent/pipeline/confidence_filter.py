"""Precision filtering with tunable thresholds."""

from __future__ import annotations

from pr_review_agent.config import Config
from pr_review_agent.pipeline.reviewer import ReviewIssue


class ConfidenceFilter:
    """Filter review issues by confidence threshold.

    Uses per-category thresholds to suppress low-signal categories
    (style, naming, refactoring) while keeping high-value bugs
    with lower thresholds.
    """

    def __init__(self, config: Config):
        self.base_threshold = config.base_confidence_threshold
        self.category_thresholds = config.category_thresholds

    def filter(self, issues: list[ReviewIssue]) -> list[ReviewIssue]:
        """Filter issues by confidence threshold and deduplicate.

        Args:
            issues: Raw review issues from the reviewer.

        Returns:
            Filtered and deduplicated list of issues.
        """
        # Confidence filter
        filtered = []
        for issue in issues:
            threshold = self._get_threshold(issue.category)
            if issue.confidence >= threshold:
                filtered.append(issue)

        # Deduplicate: remove issues with very similar comments
        return self._deduplicate(filtered)

    def _deduplicate(self, issues: list[ReviewIssue]) -> list[ReviewIssue]:
        """Remove near-duplicate issues, keeping highest confidence."""
        if len(issues) <= 1:
            return issues

        # Sort by confidence descending
        issues.sort(key=lambda x: x.confidence, reverse=True)
        kept = []
        for issue in issues:
            is_dup = False
            # Compare against kept issues
            for existing in kept:
                if self._is_similar(issue.comment, existing.comment):
                    is_dup = True
                    break
            if not is_dup:
                kept.append(issue)
        return kept

    @staticmethod
    def _is_similar(a: str, b: str) -> bool:
        """Check if two comments describe the same issue."""
        # Normalize
        a_norm = a.lower().strip()[:200]
        b_norm = b.lower().strip()[:200]

        # Exact prefix match
        if a_norm[:80] == b_norm[:80]:
            return True

        # Word overlap
        a_words = set(a_norm.split())
        b_words = set(b_norm.split())
        if not a_words or not b_words:
            return False
        overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
        return overlap > 0.75

    def _get_threshold(self, category: str) -> float:
        """Get confidence threshold for a category."""
        # Normalize category name
        normalized = category.lower().replace(" ", "_").replace("-", "_")

        # Check direct match
        if normalized in self.category_thresholds:
            return self.category_thresholds[normalized]

        # Check partial matches
        for key, threshold in self.category_thresholds.items():
            if key in normalized or normalized in key:
                return threshold

        return self.base_threshold

    def get_stats(self, before: list[ReviewIssue], after: list[ReviewIssue]) -> dict:
        """Get filtering statistics."""
        removed = len(before) - len(after)
        by_category = {}
        for issue in before:
            cat = issue.category.lower()
            if cat not in by_category:
                by_category[cat] = {"total": 0, "kept": 0, "removed": 0}
            by_category[cat]["total"] += 1

        for issue in after:
            cat = issue.category.lower()
            if cat in by_category:
                by_category[cat]["kept"] += 1

        for cat in by_category:
            by_category[cat]["removed"] = (
                by_category[cat]["total"] - by_category[cat]["kept"]
            )

        return {
            "total_before": len(before),
            "total_after": len(after),
            "removed": removed,
            "by_category": by_category,
        }
