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

    def __init__(self, config: Config, base_threshold_override: float | None = None):
        self.base_threshold = base_threshold_override if base_threshold_override is not None else config.base_confidence_threshold
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
        """Remove near-duplicate issues, keeping highest confidence.

        Uses a multi-layered approach:
        1. Same file + nearby lines (within 5 lines) + same category
        2. Word-level overlap (robust to rephrasing)
        3. Character-level SequenceMatcher (catches near-identical text)
        4. Bug signature matching (keyword extraction for technical identifiers)

        This aggressively deduplicates the same conceptual bug across files
        (e.g., "forEach with async" in 3 files -> 1 report).
        """
        from difflib import SequenceMatcher

        if len(issues) <= 1:
            return issues

        # Common stop words for word overlap
        stop_words = {
            "the", "a", "an", "is", "in", "to", "of", "and", "or", "but",
            "for", "on", "at", "by", "with", "from", "this", "that", "it",
            "not", "be", "are", "was", "will", "can", "should", "could",
            "may", "which", "when", "if", "has", "have", "does", "do",
            "same", "also", "as", "here", "too", "so", "its", "been",
        }

        # Sort by confidence descending, then severity (critical first)
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        issues.sort(key=lambda x: (-x.confidence, severity_order.get(x.severity, 4)))
        kept: list[ReviewIssue] = []
        for issue in issues:
            is_dup = False
            i_lower = issue.comment.lower()[:300]
            i_words = set(i_lower.split()) - stop_words
            i_sig = self._extract_bug_signature(issue.comment)

            for existing in kept:
                e_lower = existing.comment.lower()[:300]
                same_file = issue.file_path == existing.file_path

                # Layer 1: Same file + nearby lines + same category
                if (same_file and
                    issue.line_number > 0 and existing.line_number > 0 and
                    abs(issue.line_number - existing.line_number) <= 5 and
                    issue.category == existing.category):
                    is_dup = True
                    break

                # Layer 2: Word-level overlap (robust to rephrasing)
                e_words = set(e_lower.split()) - stop_words
                if i_words and e_words:
                    overlap = len(i_words & e_words) / min(len(i_words), len(e_words))
                    if same_file and overlap > 0.50:
                        is_dup = True
                        break
                    if not same_file and issue.category == existing.category and overlap > 0.55:
                        is_dup = True
                        break
                    if not same_file and overlap > 0.65:
                        is_dup = True
                        break

                # Layer 3: Character-level similarity (near-identical text)
                ratio = SequenceMatcher(None, i_lower, e_lower).ratio()
                if same_file and ratio > 0.55:
                    is_dup = True
                    break
                if not same_file and ratio > 0.70:
                    is_dup = True
                    break

                # Layer 4: Bug signature matching (keyword-based)
                if i_sig:
                    e_sig = self._extract_bug_signature(existing.comment)
                    if e_sig and self._signatures_match(i_sig, e_sig, issue.category == existing.category):
                        is_dup = True
                        break

            if not is_dup:
                kept.append(issue)
        return kept

    @staticmethod
    def _extract_bug_signature(comment: str) -> set[str]:
        """Extract the core technical terms that identify a bug.

        Returns a set of normalized identifiers, method names, and
        key phrases that constitute the "signature" of the bug.
        """
        import re
        comment_lower = comment.lower()
        terms: set[str] = set()

        # Backtick-quoted identifiers (e.g., `forEach`, `recordLegacyDuration`)
        terms.update(re.findall(r'`([^`]+)`', comment_lower))

        # camelCase/PascalCase identifiers (4+ chars with internal caps)
        for w in re.findall(r'[a-zA-Z_]\w{3,}', comment):
            if any(c.isupper() for c in w[1:]) or '_' in w:
                terms.add(w.lower())

        # Technical compound phrases (2-3 word patterns)
        tech_patterns = [
            r'foreach\s+(?:with\s+)?async',
            r'negative\s+(?:indexing|slicing|offset)',
            r'null\s+(?:reference|dereference|pointer)',
            r'race\s+condition',
            r'copy[- ]paste\s+error',
            r'inverted?\s+(?:logic|condition)',
            r'wrong\s+(?:method|function|parameter|variable|log\s+level)',
            r'dead\s+code',
            r'lock\s+scope',
            r'double[- ]checked\s+locking',
            r'sql\s+injection',
            r'xss\s+vulnerabilit',
            r'ssrf\s+vulnerabilit',
            r'origin\s+validation',
        ]
        for pat in tech_patterns:
            if re.search(pat, comment_lower):
                terms.add(re.sub(r'\s+', '_', pat.replace(r'\s+', ' ').replace('?', '').replace(r'[- ]', '_')))

        return terms

    @staticmethod
    def _signatures_match(sig_a: set[str], sig_b: set[str], same_category: bool) -> bool:
        """Check if two bug signatures represent the same bug."""
        if not sig_a or not sig_b:
            return False
        shared = sig_a & sig_b
        # Same category + 2 shared terms = same bug
        if same_category and len(shared) >= 2:
            return True
        # 3+ shared terms regardless of category
        if len(shared) >= 3:
            return True
        # High Jaccard overlap for smaller sets
        union = sig_a | sig_b
        if union and len(shared) / len(union) > 0.5:
            return True
        return False

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
