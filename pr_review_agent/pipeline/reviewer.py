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
from pr_review_agent.warpgrep.client import (
    create_warpgrep_tool,
    execute_warpgrep_tool,
    WARPGREP_TOOL_NAME,
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
        import httpx
        self.client = anthropic.Anthropic(
            api_key=config.anthropic_api_key,
            timeout=httpx.Timeout(600.0, connect=30.0),
        )
        self.context_gatherer = context_gatherer

    def review_pr(
        self,
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
    ) -> list[ReviewIssue]:
        """Run dual-pass review on a PR with WarpGrep as a tool.

        Claude gets the WarpGrep tool so it can search the codebase on-demand
        during each review pass. No separate pre-fetch phase needed.

        Pass 1: Comprehensive bug-finding with WarpGrep tool available.
        Pass 2: Second pass with different focus, also with WarpGrep tool.
        Merge: Combine unique issues, boost confirmed ones.
        Validate: WarpGrep validation to kill remaining FPs.
        """
        # Pass 1 + Pass 2 with WarpGrep tool available
        pass1_issues = self._pass1_review(file_diffs, repo_path=repo_path)
        pass2_issues = self._pass2_review(file_diffs, repo_path=repo_path)

        # Merge
        all_issues = self._merge_passes(pass1_issues, pass2_issues)

        # Validate issues with WarpGrep (kill FPs)
        if all_issues and repo_path and self.config.warpgrep_validate_issues and self.context_gatherer:
            all_issues = self._validate_with_warpgrep(all_issues, repo_path)

        return all_issues

    def _build_diff_text(self, file_diffs: list[FileDiff], max_chars: int = 80000) -> str:
        """Build combined diff text from file diffs."""
        diff_sections = []
        total_chars = 0

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

        return "\n".join(diff_sections)

    def _build_context_section(self, file_contexts: dict[str, str]) -> str:
        """Build context section from WarpGrep results."""
        if not file_contexts:
            return ""

        parts = ["## Codebase Context (from repository search)",
                 "The following code exists in the repository outside the diff. "
                 "Use this to verify whether functions/variables exist before claiming they're undefined, "
                 "understand codebase patterns, and check if callers match function signature changes.\n"]

        total_chars = 0
        for file_path, ctx in file_contexts.items():
            if total_chars > 15000:
                break
            section = f"### Context for {file_path}\n{ctx}\n"
            parts.append(section)
            total_chars += len(section)

        return "\n".join(parts)

    def _pass1_review(self, file_diffs: list[FileDiff], repo_path: str | None = None) -> list[ReviewIssue]:
        """Pass 1: Comprehensive bug-finding review with WarpGrep tool."""
        combined_diff = self._build_diff_text(file_diffs)

        # Gather language hints
        languages = {fd.language for fd in file_diffs}
        lang_hints = "\n\n".join(
            f"**{lang}:**\n{get_language_hint(lang)}"
            for lang in languages
            if get_language_hint(lang)
        )

        warpgrep_instruction = ""
        if repo_path and self.config.morph_api_key:
            warpgrep_instruction = """
## WarpGrep Tool Available
You have access to a `warpgrep_codebase_search` tool that searches this repository.
USE IT to verify claims before reporting issues:
- Before claiming a function/variable is "undefined" or "missing", search for it
- Before claiming an import is missing, search for it
- To understand how a changed function is called, search for its callers
- To check type definitions or interfaces, search for them
This tool is your key advantage. Use it proactively to avoid false positives.
"""

        prompt = f"""Review this pull request for bugs and correctness issues.

## Changed Files ({len(file_diffs)} files)
{combined_diff}

{f"## Language-Specific Notes{chr(10)}{lang_hints}" if lang_hints else ""}
{warpgrep_instruction}
## What to Look For
Focus ONLY on changed lines (+ and - lines). Find issues that WILL cause incorrect behavior:

1. **Wrong variable/value**: Copy-paste errors, wrong parameter checked, wrong string constant, wrong method called (e.g. recordLegacyDuration vs recordStorageDuration), returning original instead of modified copy
2. **Wrong locale/translation**: Text in wrong language for locale file
3. **Inverted/wrong logic**: Condition backwards (AND vs OR), function always returns same value, unreachable branches, inverted comparison
4. **API misuse**: Method doesn't exist, wrong parameters, abstract class not implemented, forEach with async (needs for...of + await)
5. **Race condition**: Lock scope reduced, thread not joined, stale reads under concurrency, double-checked locking missing second check
6. **Null/nil dereference**: Accessing member on value that IS null/undefined/nil in a concrete path
7. **Type mismatch**: math ops on datetime, Django negative QuerySet slice, object reference comparison instead of value comparison (=== on dayjs), case-sensitive comparison when case-insensitive needed
8. **Security**: SSRF (open with user URL), X-Frame-Options weakened, permission check removed, auth bypass, origin validation bypassable
9. **Broken test**: Name typo, comment contradicts assertion, HTTP method mismatch, mock makes test vacuous, monkeypatch invalidates test logic
10. **Framework bugs**: Class field evaluated at definition time (datetime.now as default), missing import, fabricator in wrong file, Rails missing ? suffix on predicate
11. **Contract violations**: Returns null when contract says non-null, deletion logic affects wrong types, side effects in read-only method, empty data prevents @updatedAt
12. **CSS bugs**: Wrong lightness/color value in conversion, mixing float with flexbox, invalid vendor prefix

## Also Report
- Method/function name typos that affect behavior (test won't be discovered, method won't match interface)
- Property name typos (misspelled identifiers)
- Dead code where results are computed but discarded
- Docstring/comment that contradicts what code actually does
- Wrong log level (Error for non-error information)
- Hardcoded values that ignore configurable settings
- Interface contract changes that break existing implementations

## CRITICAL: Avoid False Positives
- Do NOT claim a variable/function/import is "undefined" or "missing" unless you can PROVE it from the diff. The diff only shows changed lines - the rest of the file exists but isn't shown. If a function is called and you don't see its definition in the diff, it's likely defined elsewhere in the file.
- Do NOT claim a variable is "used before defined" unless you can prove the ordering from the diff.
- Do NOT claim a module is "not imported" unless you see the file's complete import section in the diff and it's definitely missing.
- When in doubt about whether something exists outside the diff, DO NOT report it.

## Rules
- Point to the EXACT line and explain WHY it's wrong
- Each comment must be SELF-CONTAINED
- Return [] if no real bugs exist
- Do NOT report: pure formatting preferences, defensive programming for impossible paths, general performance suggestions

Return a JSON array:
{{"file_path": "...", "line_number": N, "category": "logic_error|incorrect_value|api_misuse|race_condition|null_reference|type_error|security|localization|test_correctness|portability", "severity": "critical|high|medium|low", "confidence": 0.5-1.0, "comment": "Describe the bug: what code is wrong, what it should be, and the consequence."}}"""

        response_text = self._call_opus(prompt, repo_path=repo_path)
        issues = self._parse_issues(response_text, source_pass="pass1")

        # Retry once if we got 0 issues from a non-trivial diff
        total_added = sum(fd.total_added for fd in file_diffs)
        if not issues and total_added > 20:
            response_text = self._call_opus(prompt, repo_path=repo_path)
            issues = self._parse_issues(response_text, source_pass="pass1_retry")

        return issues

    def _pass2_review(self, file_diffs: list[FileDiff], repo_path: str | None = None) -> list[ReviewIssue]:
        """Pass 2: Different focus to catch bugs missed by first pass."""
        combined_diff = self._build_diff_text(file_diffs)

        warpgrep_instruction = ""
        if repo_path and self.config.morph_api_key:
            warpgrep_instruction = """
## WarpGrep Tool Available
You have access to a `warpgrep_codebase_search` tool. Use it to verify any claims about missing definitions, undefined variables, or broken contracts before reporting them.
"""

        prompt = f"""Review this PR diff with fresh eyes. Look for bugs that a FIRST reviewer commonly misses.

## Changed Files
{combined_diff}

{warpgrep_instruction}

## Commonly Missed Bug Patterns

Check each of these SPECIFICALLY against the diff:

1. **Behavioral regressions from removals**: Did the PR remove safety checks, filters, permission checks, or error handling that was protecting against something? Look for removed .filter(), removed if-guards, removed permission checks.

2. **Cross-file inconsistency**: Are metric tags, string constants, or key names consistent across all files? (e.g., 'shard' vs 'shards'). Do function signatures match their call sites?

3. **Concurrency / ordering bugs**: Does code assume dict ordering? Does zip() assume aligned iteration? Are there stale variable reads that could race? Are threads/processes properly joined/waited?

4. **Return value confusion**: Does a function return a wrapper (SafeParseResult, Optional, Promise) when callers expect the unwrapped value? Does it return the original variable when it should return the modified copy?

5. **Framework contract violations**: In Ruby, does before_validation work on nil? In Python, is a class field mutable default? In TypeScript, does === compare objects by reference? In Go, does Exec() expect (query, args...) format?

6. **Test validity**: Does a monkeypatch make the thing being tested a no-op? Does the test HTTP method match the route? Does the test name match what it tests? Do test assertions match test comments?

7. **Scope/naming errors**: Is a variable referenced from the wrong scope? Is a property name misspelled? Does a method name have a typo that prevents it from being called?

8. **Security surface changes**: Any weakening of auth, frame options, origin validation, URL validation, or input sanitization?

9. **CSS value errors**: In color conversions or theme changes, are lightness/opacity values correct? Are vendor prefixes valid?

10. **Import/definition errors**: Are all referenced modules imported? Are all used functions/methods defined? Does method redefinition accidentally overwrite a previous version?

Also check for:
- Method/function name typos that prevent discovery or matching
- Property name typos (misspelled identifiers)
- Dead code where results are computed then discarded
- Docstring that contradicts implementation
- Wrong log level (Error for debug info)
- Hardcoded values ignoring configuration

CRITICAL: Do NOT claim variables/functions are "undefined" or "not imported" unless you can PROVE it from the diff. The diff only shows changed lines - definitions may exist in the file outside the diff context.

Each comment must be SELF-CONTAINED: name the specific code element, state what's wrong, and explain the runtime consequence.
Return [] if nothing found.

{{"file_path": "...", "line_number": N, "category": "logic_error|incorrect_value|api_misuse|race_condition|null_reference|type_error|security|localization|test_correctness|portability", "severity": "critical|high|medium|low", "confidence": 0.5-1.0, "comment": "Describe the specific bug."}}"""

        response_text = self._call_opus(prompt, thinking_budget=10000, repo_path=repo_path)
        return self._parse_issues(response_text, source_pass="pass2")

    def _merge_passes(
        self, pass1: list[ReviewIssue], pass2: list[ReviewIssue]
    ) -> list[ReviewIssue]:
        """Merge issues from two passes, deduplicating similar findings.

        Issues found in both passes get a confidence boost.
        """
        confirmed_p1 = set()
        merged = list(pass1)

        for p2_issue in pass2:
            is_dup = False
            for idx, p1_issue in enumerate(merged):
                if self._issues_same(p1_issue, p2_issue):
                    # Boost confidence for issues found in both passes
                    p1_issue.confidence = min(1.0, p1_issue.confidence + 0.1)
                    confirmed_p1.add(idx)
                    is_dup = True
                    break
            if not is_dup:
                merged.append(p2_issue)

        # Penalize issues only found in a single pass (likely FPs)
        for idx, issue in enumerate(merged):
            if idx < len(pass1) and idx not in confirmed_p1:
                issue.confidence = max(0.0, issue.confidence - 0.1)
        # Pass2-only issues (appended after pass1) are also single-pass
        for idx in range(len(pass1), len(merged)):
            merged[idx].confidence = max(0.0, merged[idx].confidence - 0.1)

        return merged

    @staticmethod
    def _issues_same(a: ReviewIssue, b: ReviewIssue) -> bool:
        """Check if two issues describe the same bug (stricter than dedup)."""
        # Must be same file
        if a.file_path != b.file_path:
            return False
        # Same line (within 5 lines)
        if a.line_number > 0 and b.line_number > 0 and abs(a.line_number - b.line_number) <= 5:
            return True
        # High word overlap in comments
        stop_words = {"the", "a", "an", "is", "in", "to", "of", "and", "or", "but",
                       "for", "on", "at", "by", "with", "from", "this", "that", "it",
                       "not", "be", "are", "was", "will", "can", "should", "could"}
        a_words = set(a.comment.lower()[:200].split()) - stop_words
        b_words = set(b.comment.lower()[:200].split()) - stop_words
        if not a_words or not b_words:
            return False
        overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
        return overlap > 0.6

    def _validate_with_warpgrep(
        self, issues: list[ReviewIssue], repo_path: str
    ) -> list[ReviewIssue]:
        """Validate issue claims against the actual codebase using WarpGrep.

        For issues that claim something is undefined/missing, search the codebase
        to verify. Drop issues that WarpGrep proves are false positives.
        """
        import re
        import sys

        validated = []
        for issue in issues:
            comment_lower = issue.comment.lower()

            # Check if this issue makes a "missing/undefined" claim
            undefined_patterns = [
                "is undefined", "is not defined", "is not imported",
                "does not exist", "never defined", "missing import",
                "not imported", "without importing",
            ]

            needs_validation = any(p in comment_lower for p in undefined_patterns)

            if needs_validation and self.context_gatherer:
                # Extract the identifier being claimed as missing
                identifiers = re.findall(
                    r'[`\'"](\w+)[`\'"]',
                    issue.comment,
                )
                if not identifiers:
                    # Try to extract from common patterns like "X is undefined"
                    m = re.search(r'(\w+)\s+(?:is|are)\s+(?:not\s+)?(?:defined|imported|declared)', issue.comment)
                    if m:
                        identifiers = [m.group(1)]

                if identifiers:
                    search_term = identifiers[0]
                    try:
                        ctx = self.context_gatherer.client.search(
                            f"definition of {search_term}",
                            repo_path,
                            max_turns=2,
                        )
                        # If we found it in the codebase, it's a false positive
                        if ctx and search_term in ctx and len(ctx) > 50:
                            print(f"  WarpGrep DISPROVED: '{search_term}' exists in codebase, dropping FP", file=sys.stderr)
                            continue
                    except Exception:
                        pass  # If validation fails, keep the issue

            validated.append(issue)

        dropped = len(issues) - len(validated)
        if dropped:
            print(f"  WarpGrep validation: dropped {dropped} FPs", file=sys.stderr)

        return validated

    def _calibration_pass(
        self, issues: list[ReviewIssue], codebase_patterns: str
    ) -> list[ReviewIssue]:
        """Validate findings against codebase patterns."""
        issues_json = json.dumps([i.to_dict() for i in issues], indent=2)

        prompt = CALIBRATION_PROMPT.format(
            issues_json=issues_json,
            codebase_patterns=codebase_patterns,
        )

        response_text = self._call_opus(prompt)
        calibrated = self._parse_issues(response_text, source_pass="calibration")

        if calibrated:
            return calibrated
        return issues

    def _call_opus(self, prompt: str, thinking_budget: int = 10000, repo_path: str | None = None) -> str:
        """Call Claude with extended thinking, optionally with WarpGrep tool.

        If repo_path is provided and WarpGrep is configured, Claude gets the
        WarpGrep tool and can search the codebase on-demand during review.
        """
        import sys

        # Build tool list if repo_path available
        tools = None
        warpgrep_tool_def = None
        if repo_path and self.config.morph_api_key:
            warpgrep_tool_def = create_warpgrep_tool(repo_path, self.config.morph_api_key)
            tools = [{
                "name": warpgrep_tool_def["name"],
                "description": warpgrep_tool_def["description"],
                "input_schema": warpgrep_tool_def["input_schema"],
            }]

        for attempt in range(2):
            text_parts = []
            messages = [{"role": "user", "content": prompt}]

            try:
                # If we have tools, use a non-streaming agentic loop
                if tools:
                    return self._agentic_loop(messages, tools, warpgrep_tool_def, thinking_budget)

                # Otherwise, use streaming (no tools)
                with self.client.messages.stream(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    thinking={"type": "adaptive"},
                    temperature=1,
                    system=SYSTEM_PROMPT,
                    messages=messages,
                ) as stream:
                    for event in stream:
                        if hasattr(event, 'type'):
                            if event.type == 'content_block_start':
                                if hasattr(event, 'content_block') and event.content_block.type == 'text':
                                    text_parts.append("")
                            elif event.type == 'content_block_delta':
                                if hasattr(event, 'delta') and event.delta.type == 'text_delta':
                                    text_parts.append(event.delta.text)

                result = "".join(text_parts)
                if result.strip():
                    return result

                if attempt == 0:
                    print(f"  WARNING: Empty response, retrying...", file=sys.stderr)
                    continue
                return "[]"

            except Exception as e:
                if attempt == 0:
                    print(f"  Stream error: {e}, retrying...", file=sys.stderr)
                    continue
                print(f"  Stream error on retry: {e}", file=sys.stderr)
                return "[]"

        return "[]"

    def _agentic_loop(
        self,
        messages: list[dict],
        tools: list[dict],
        warpgrep_tool_def: dict,
        thinking_budget: int,
        max_tool_rounds: int = 3,
    ) -> str:
        """Run Claude with WarpGrep tool in an agentic loop.

        Claude can call WarpGrep to search the codebase, get results back,
        and continue reasoning before producing its final review output.
        """
        import sys

        for round_num in range(max_tool_rounds + 1):
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                thinking={"type": "adaptive"},
                temperature=1,
                system=SYSTEM_PROMPT,
                tools=tools,
                messages=messages,
            )

            # Collect text blocks
            text_parts = []
            tool_use_blocks = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_use_blocks.append(block)

            # If no tool calls, we're done
            if not tool_use_blocks or response.stop_reason == "end_turn":
                return "\n".join(text_parts) if text_parts else "[]"

            # Execute tool calls and feed results back
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for tool_block in tool_use_blocks:
                if tool_block.name == WARPGREP_TOOL_NAME:
                    print(f"  WarpGrep search: {tool_block.input.get('search_string', '')[:80]}", file=sys.stderr)
                    result_text = execute_warpgrep_tool(tool_block.input, warpgrep_tool_def)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_block.id,
                        "content": result_text[:15000] if result_text else "No results found.",
                    })

            messages.append({"role": "user", "content": tool_results})

        # If we exhausted rounds, get final text
        return "\n".join(text_parts) if text_parts else "[]"

    def _parse_issues(self, response_text: str, source_pass: str) -> list[ReviewIssue]:
        """Parse JSON issues from LLM response."""
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
