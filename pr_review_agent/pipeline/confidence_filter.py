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
        """Filter issues by confidence threshold, FP heuristics, and deduplicate.

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

        # Remove likely false positives about undefined/missing things
        filtered = [i for i in filtered if not self._is_likely_undefined_fp(i)]

        # Deduplicate: remove issues with very similar comments
        return self._deduplicate(filtered)

    @staticmethod
    def _is_likely_undefined_fp(issue: ReviewIssue) -> bool:
        """Check if issue is likely a false positive about undefined variables.

        Common FP pattern: model claims variable/function is undefined or not imported,
        but it actually exists in the file outside the diff context.
        """
        comment_lower = issue.comment.lower()

        # Patterns that indicate "undefined variable" claims
        undefined_patterns = [
            "is undefined",
            "is not defined",
            "is not imported",
            "is not declared",
            "does not exist",
            "is used before",
            "used before it",
            "before definition",
            "never defined",
            "never imported",
            "never declared",
            "not imported in",
            "without importing",
            "missing import for",
        ]

        # Check if the comment is primarily about something being undefined
        for pattern in undefined_patterns:
            if pattern in comment_lower:
                # Exception: if it's about a specific API method that genuinely doesn't exist
                # (like queue.shutdown() or picocli.exit()), keep it
                api_exceptions = [
                    "method does not exist",
                    "function does not exist",
                    "module does not exist",
                    "class does not exist",
                    "does not exist in the standard",
                    "does not exist on",
                    "no such method",
                    "attributeerror",
                    "nameerror",
                    "nomethoderror",
                ]
                for exc in api_exceptions:
                    if exc in comment_lower:
                        return False  # Keep these - they're about genuine API misuse

                # If it's a generic "X is undefined" claim, likely FP
                # unless confidence is very high (0.95+)
                if issue.confidence < 0.95:
                    return True

        return False

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
                # Same file + same line = almost certainly same issue
                if (issue.file_path == existing.file_path and
                    issue.line_number > 0 and existing.line_number > 0 and
                    abs(issue.line_number - existing.line_number) <= 2):
                    is_dup = True
                    break
                # Same file + very similar comment text
                if (issue.file_path == existing.file_path and
                    self._is_similar(issue.comment, existing.comment)):
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
        if a_norm[:60] == b_norm[:60]:
            return True

        # Word overlap (excluding common words)
        stop_words = {"the", "a", "an", "is", "in", "to", "of", "and", "or", "but",
                       "for", "on", "at", "by", "with", "from", "this", "that", "it",
                       "not", "be", "are", "was", "will", "can", "should", "could",
                       "may", "which", "when", "if", "has", "have", "does", "do"}
        a_words = set(a_norm.split()) - stop_words
        b_words = set(b_norm.split()) - stop_words
        if not a_words or not b_words:
            return False
        overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
        return overlap > 0.65

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
