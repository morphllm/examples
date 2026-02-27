"""Opus 4.6 multi-pass review engine."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import anthropic

from pr_review_agent.config import Config
from pr_review_agent.pipeline.context_gatherer import ContextGatherer
from pr_review_agent.pipeline.diff_parser import FileDiff
from pr_review_agent.prompts.review import get_language_hint
from pr_review_agent.prompts.system import (
    CALIBRATION_PROMPT,
    CROSS_FILE_PROMPT,
    FILE_REVIEW_PROMPT,
    SYSTEM_PROMPT,
)


@dataclass
class ReviewIssue:
    """A single issue found during review."""
    file_path: str
    line_number: int
    category: str
    severity: str
    confidence: float
    comment: str
    source_pass: str = "file_review"
    calibration_note: str = ""

    def to_dict(self) -> dict:
        d = {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "category": self.category,
            "severity": self.severity,
            "confidence": self.confidence,
            "comment": self.comment,
        }
        if self.calibration_note:
            d["calibration_note"] = self.calibration_note
        return d


@dataclass
class ReviewResult:
    """Result of reviewing a single PR."""
    pr_url: str
    issues: list[ReviewIssue] = field(default_factory=list)
    file_count: int = 0
    total_added_lines: int = 0
    error: str | None = None


class Reviewer:
    """Multi-pass code reviewer using Opus 4.6."""

    def __init__(self, config: Config, context_gatherer: ContextGatherer | None = None):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        self.context_gatherer = context_gatherer

    def review_pr(
        self,
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
    ) -> list[ReviewIssue]:
        """Run multi-pass review on a PR.

        Pass 1: Initial review with precision-focused prompt
        Pass 2: Second review with different emphasis (for recall)
        Merge: Union of issues from both passes (dedup by similarity)

        Args:
            file_diffs: Parsed diffs for all changed files.
            repo_path: Path to cloned repo (for WarpGrep context).

        Returns:
            List of review issues.
        """
        # Pass 1: Standard precision-focused review
        pass1_issues = self._batched_review(file_diffs)

        # Pass 2: Second pass focusing on subtle/cross-file bugs
        pass2_issues = self._second_pass_review(file_diffs)

        # Merge: combine unique issues from both passes
        all_issues = self._merge_passes(pass1_issues, pass2_issues)

        # Optional calibration with WarpGrep context
        if all_issues and self.context_gatherer and repo_path:
            codebase_patterns = self.context_gatherer.gather_codebase_patterns(
                file_diffs, repo_path
            )
            all_issues = self._calibration_pass(all_issues, codebase_patterns)

        return all_issues

    def _batched_review(self, file_diffs: list[FileDiff]) -> list[ReviewIssue]:
        """Review all files in a single Opus call for speed.

        Batches all diffs into one prompt (up to context limits).
        Falls back to per-file review for very large PRs.
        """
        # Build combined diff text
        diff_sections = []
        total_chars = 0
        max_chars = 60000  # Leave room for system prompt + response

        for fd in file_diffs:
            section = f"\n### File: {fd.file_path} ({fd.language})\n```diff\n{fd.raw_diff}\n```"
            if total_chars + len(section) > max_chars:
                # Truncate this file's diff
                remaining = max_chars - total_chars - 200
                if remaining > 500:
                    section = f"\n### File: {fd.file_path} ({fd.language})\n```diff\n{fd.raw_diff[:remaining]}\n[truncated]\n```"
                    diff_sections.append(section)
                break
            diff_sections.append(section)
            total_chars += len(section)

        # Gather language hints
        languages = {fd.language for fd in file_diffs}
        lang_hints = "\n\n".join(
            f"**{lang}:**\n{get_language_hint(lang)}"
            for lang in languages
            if get_language_hint(lang)
        )

        combined_diff = "\n".join(diff_sections)

        prompt = f"""Review this pull request for definite bugs only.

## Changed Files ({len(file_diffs)} files)
{combined_diff}

## What to look for
Focus ONLY on the changed lines (+ and - lines). Find issues that WILL cause incorrect behavior:

1. **Wrong variable/value used**: Copy-paste errors, wrong parameter in error message, wrong string constant, wrong locale text
2. **Inverted/wrong logic**: Condition that's backwards, comparison that should be different, off-by-one that changes behavior
3. **API called incorrectly**: Method doesn't exist on this type, wrong number/type of parameters, return value used wrong
4. **Race condition from removed lock**: Lock/synchronization that was removed but is still needed for concurrent access
5. **Null dereference that WILL happen**: Not "might be null" but IS null based on the code flow shown
6. **Type mismatch causing runtime error**: Wrong type passed that will crash or silently corrupt data
7. **Security flaw**: SQL injection, SSRF, auth bypass, XSS
8. **Broken test**: Test that passes vacuously due to mocking, or tests timing-dependent behavior with fixed sleep
9. **Cross-file inconsistency**: Function signature changed but caller not updated, removed export still imported
10. **async/await bug**: forEach with async callback, missing await, unhandled promise
11. **Platform bug**: macOS-only command in cross-platform code

## Rules
- ONLY report issues where you can point to the EXACT line and explain WHY it's wrong
- If you're not at least 70% sure it's a real bug, don't report it
- "Missing validation" is NOT a bug unless you can show the invalid input path
- "Could be null" is NOT a bug unless you can trace the null through the code
- An empty result [] is perfectly fine if no real bugs exist
- Do NOT report: style, naming, docs, performance, defensive programming suggestions

Return a JSON array (or [] if no bugs):
{{"file_path": "...", "line_number": N, "category": "logic_error|incorrect_value|api_misuse|race_condition|null_reference|type_error|security|localization|test_correctness|portability", "severity": "critical|high|medium|low", "confidence": 0.5-1.0, "comment": "[Element] does X but should do Y, causing Z."}}"""

        response_text = self._call_opus(prompt)
        issues = self._parse_issues(response_text, source_pass="batched_review")

        # Retry once if we got 0 issues from a non-trivial diff (likely parse/API issue)
        total_added = sum(fd.total_added for fd in file_diffs)
        if not issues and total_added > 10:
            response_text = self._call_opus(prompt)
            issues = self._parse_issues(response_text, source_pass="batched_review")

        return issues

    def _second_pass_review(self, file_diffs: list[FileDiff]) -> list[ReviewIssue]:
        """Second review pass focusing on different bug types for higher recall.

        This pass emphasizes subtle bugs that the first pass might miss:
        copy-paste errors, localization, cross-file inconsistencies, test issues.
        """
        # Build combined diff text (same as batched review)
        diff_sections = []
        total_chars = 0
        max_chars = 60000

        for fd in file_diffs:
            section = f"\n### File: {fd.file_path} ({fd.language})\n```diff\n{fd.raw_diff}\n```"
            if total_chars + len(section) > max_chars:
                remaining = max_chars - total_chars - 200
                if remaining > 500:
                    section = f"\n### File: {fd.file_path} ({fd.language})\n```diff\n{fd.raw_diff[:remaining]}\n[truncated]\n```"
                    diff_sections.append(section)
                break
            diff_sections.append(section)
            total_chars += len(section)

        combined_diff = "\n".join(diff_sections)

        prompt = f"""Look at this PR diff with fresh eyes. Focus on these specific bug types that are easy to miss:

## Changed Files
{combined_diff}

## Focus Areas (look ONLY for these)
1. **Copy-paste bugs**: Same code copied but a variable/string/constant wasn't updated. Wrong error message text. Wrong parameter name in null check.
2. **Wrong locale/translation**: Translation text in the wrong language for the locale file it's in.
3. **Inverted logic**: Boolean condition that's backwards, comparison operator that should be different (== vs !=, < vs >).
4. **Async bugs**: forEach with async callback (doesn't await), missing await, unhandled promise rejection.
5. **Cross-file mismatch**: Function signature changed in one file but callers in another file not updated.
6. **Test that doesn't test**: Mock/patch that makes the assertion vacuous, flaky sleep-based timing, test name doesn't match what it tests.
7. **Platform-specific**: macOS-only command syntax (sed -i ''), Windows-only path separators.
8. **Type confusion**: Object comparison with === (always false for objects), wrong type passed silently.

Only report issues where you can point to the EXACT problematic line and explain the concrete consequence.
Return [] if nothing found.

{{"file_path": "...", "line_number": N, "category": "logic_error|incorrect_value|api_misuse|race_condition|null_reference|type_error|security|localization|test_correctness|portability", "severity": "critical|high|medium|low", "confidence": 0.5-1.0, "comment": "[Element] does X but should do Y, causing Z."}}"""

        response_text = self._call_opus(prompt)
        return self._parse_issues(response_text, source_pass="pass2")

    def _merge_passes(
        self, pass1: list[ReviewIssue], pass2: list[ReviewIssue]
    ) -> list[ReviewIssue]:
        """Merge issues from two passes, deduplicating similar findings.

        Issues found in both passes get a confidence boost.
        """
        from pr_review_agent.pipeline.confidence_filter import ConfidenceFilter

        merged = list(pass1)
        for p2_issue in pass2:
            # Check if this is a duplicate of a pass1 issue
            is_dup = False
            for p1_issue in merged:
                if (p1_issue.file_path == p2_issue.file_path and
                    ConfidenceFilter._is_similar(p1_issue.comment, p2_issue.comment)):
                    # Boost confidence for issues found in both passes
                    p1_issue.confidence = min(1.0, p1_issue.confidence + 0.1)
                    is_dup = True
                    break
            if not is_dup:
                merged.append(p2_issue)

        return merged

    def _calibration_pass(
        self, issues: list[ReviewIssue], codebase_patterns: str
    ) -> list[ReviewIssue]:
        """Pass 3: Validate findings against codebase patterns."""
        issues_json = json.dumps([i.to_dict() for i in issues], indent=2)

        prompt = CALIBRATION_PROMPT.format(
            issues_json=issues_json,
            codebase_patterns=codebase_patterns,
        )

        response_text = self._call_opus(prompt)
        calibrated = self._parse_issues(response_text, source_pass="calibration")

        # If calibration returned valid results, use them; otherwise keep originals
        if calibrated:
            return calibrated
        return issues

    def _call_opus(self, prompt: str) -> str:
        """Call Claude with extended thinking for deep bug analysis."""
        import sys

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            thinking={"type": "enabled", "budget_tokens": 10000},
            temperature=1,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        # Extract text from response (skip thinking blocks)
        for block in response.content:
            if block.type == "text":
                return block.text

        # Log unexpected empty response
        block_types = [b.type for b in response.content]
        print(f"  WARNING: No text block in response. Block types: {block_types}", file=sys.stderr)
        return "[]"

    def _parse_issues(self, response_text: str, source_pass: str) -> list[ReviewIssue]:
        """Parse JSON issues from LLM response."""
        # Try to extract JSON array from response
        text = response_text.strip()

        # Handle markdown code blocks
        if "```" in text:
            parts = text.split("```")
            for part in parts:
                cleaned = part.strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
                if cleaned.startswith("["):
                    text = cleaned
                    break

        # Find the JSON array
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1:
            return []

        try:
            items = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return []

        issues = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                issue = ReviewIssue(
                    file_path=item.get("file_path", ""),
                    line_number=int(item.get("line_number", 0)),
                    category=item.get("category", "unknown"),
                    severity=item.get("severity", "medium"),
                    confidence=float(item.get("confidence", 0.5)),
                    comment=item.get("comment", ""),
                    source_pass=source_pass,
                    calibration_note=item.get("calibration_note", ""),
                )
                issues.append(issue)
            except (ValueError, TypeError):
                continue

        return issues
