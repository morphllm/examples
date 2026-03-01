"""Opus 4.6 multi-pass review engine with shuffled ensemble + majority voting."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

import anthropic
from pydantic import BaseModel, Field

from pr_review_agent.config import Config
from pr_review_agent.pipeline.diff_parser import FileDiff
from pr_review_agent.prompts.review import get_language_hint
from pr_review_agent.prompts.system import SYSTEM_PROMPT
from pr_review_agent.warpgrep.client import (
    create_warpgrep_tool,
    execute_warpgrep_tool,
    WARPGREP_TOOL_NAME,
)


class SearchQuery(BaseModel):
    query: str = Field(description="A specific codebase search query targeting a concrete bug suspicion")
    reason: str = Field(description="Why this search matters — what bug could it reveal")


class PlannedSearches(BaseModel):
    searches: list[SearchQuery] = Field(min_length=4, max_length=4, description="Exactly 4 targeted searches")


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


class Reviewer:
    """Multi-pass code reviewer using Opus 4.6."""

    def __init__(self, config: Config):
        self.config = config
        import httpx
        self.client = anthropic.Anthropic(
            api_key=config.anthropic_api_key,
            timeout=httpx.Timeout(600.0, connect=30.0),
        )
        # Prompt overrides (set via configure_from_organism)
        self._system_prompt: str | None = None
        self._review_instructions: str | None = None
        self._judge_prompt: str | None = None
        self._num_passes: int | None = None

    def configure_from_organism(self, organism) -> None:
        """Override prompts with evolved values from a CodeReviewOrganism."""
        self._system_prompt = organism.system_prompt
        self._review_instructions = organism.review_instructions
        self._judge_prompt = organism.judge_prompt
        self._num_passes = organism.num_passes

    @property
    def active_system_prompt(self) -> str:
        return self._system_prompt if self._system_prompt is not None else SYSTEM_PROMPT

    def review_pr(
        self,
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
        num_passes: int | None = None,
    ) -> list[ReviewIssue]:
        """Run shuffled multi-pass review with majority voting.

        Phase 0: Pre-search with WarpGrep to gather codebase context.
        Passes 1-4: Each pass shuffles file order and alternates between
                     comprehensive and commonly-missed prompts.
        Pass 5: Quick-wins pass targeting low-severity but real issues
                 (typos, naming, doc mismatches).
        Merge: Majority voting with confidence adjustments.
        """
        import sys

        # Resolve num_passes: argument > organism override > default
        if num_passes is None:
            num_passes = self._num_passes if self._num_passes is not None else 6

        # Phase 0: Gather codebase context via WarpGrep (1-2 strategic searches)
        warpgrep_context = ""
        if repo_path and self.config.morph_api_key and self.config.warpgrep_tool_enabled:
            warpgrep_context = self._gather_strategic_context(file_diffs, repo_path)
            if warpgrep_context:
                ctx_len = len(warpgrep_context)
                print(f"  WarpGrep context: {ctx_len} chars", file=sys.stderr)

        # Run N passes with shuffled file ordering and 3 contrastive prompt types
        # Pass types cycle: comprehensive(0) -> commonly-missed(1) -> deep-investigation(2) -> repeat
        pass_methods = [self._pass1_review, self._pass2_review, self._pass3_review]
        all_pass_issues: list[list[ReviewIssue]] = []
        for pass_num in range(num_passes):
            # Shuffle file order for each pass (different ordering = different attention)
            shuffled = list(file_diffs)
            if pass_num > 0:  # Keep first pass in original order
                random.shuffle(shuffled)

            # Cycle through 3 contrastive pass types
            pass_method = pass_methods[pass_num % 3]
            issues = pass_method(shuffled, warpgrep_context=warpgrep_context, repo_path=repo_path)

            for issue in issues:
                issue.source_pass = f"pass{pass_num + 1}"

            all_pass_issues.append(issues)
            print(f"  Pass {pass_num + 1}/{num_passes}: {len(issues)} issues", file=sys.stderr)

        # Additional quick-wins pass for low-severity real issues
        quick_issues = self._quick_wins_review(file_diffs, warpgrep_context=warpgrep_context, repo_path=repo_path)
        if quick_issues:
            for issue in quick_issues:
                issue.source_pass = "quick_wins"
            all_pass_issues.append(quick_issues)
            print(f"  Quick-wins pass: {len(quick_issues)} issues", file=sys.stderr)

        # LLM-based aggregation: replaces majority voting + judge in one step
        aggregated = self._llm_aggregate(all_pass_issues, file_diffs, repo_path=repo_path)

        return aggregated

    def _gather_strategic_context(self, file_diffs: list[FileDiff], repo_path: str) -> str:
        """Pre-gather codebase context via Opus-planned WarpGrep searches.

        Uses structured outputs to get exactly 4 targeted search queries from
        Opus, then executes them in parallel via WarpGrep.
        """
        import sys
        from concurrent.futures import ThreadPoolExecutor, as_completed
        from pr_review_agent.warpgrep.client import search_codebase_text

        # Step 1: Build compact diff for the query planner (cap at 30K chars)
        compact_diff = self._build_diff_text(file_diffs, max_chars=30000)

        # Step 2: Ask Opus to plan the searches
        planner_prompt = f"""You are about to review this PR diff for bugs. Before reviewing, you can search the codebase for context that will help you find real bugs.

## PR Diff
{compact_diff}

## What searches would help you find BUGS in this diff?

Think about what could go wrong with these specific changes:
- If a function signature or behavior changed, who calls it? Will callers break with the new behavior?
- If a lock, guard, or safety check was removed, what code depended on that protection?
- If a type, interface, or abstract class changed, do implementations still satisfy the contract?
- If error handling changed, what callers expect the old error behavior?
- If string constants, metric tags, or enum values changed, are they consistent across producer and consumer?
- If a class field has a potentially problematic default value, how is it instantiated?

Generate exactly 4 search queries. Each should be a SPECIFIC question about this codebase targeting a SPECIFIC potential bug you see in the diff.

Do NOT generate generic queries like "find test files" or "find related code" or "find dependencies". Every query must target a concrete suspicion."""

        try:
            planner_response = self.client.messages.create(
                model=self.config.model,
                max_tokens=8000,
                temperature=1,
                thinking={"type": "enabled", "budget_tokens": 4000},
                messages=[{"role": "user", "content": planner_prompt}],
                output_config={
                    "format": {
                        "type": "json_schema",
                        "schema": PlannedSearches.model_json_schema(),
                    }
                },
            )

            # With thinking enabled, content has thinking blocks then text block
            text_block = next(b for b in planner_response.content if b.type == "text")
            parsed = json.loads(text_block.text)
            result = PlannedSearches(**parsed)
            queries = [(s.query, s.reason) for s in result.searches]
        except Exception as e:
            print(f"  Query planner failed: {e}, falling back to basic search", file=sys.stderr)
            queries = self._fallback_queries(file_diffs)

        if not queries:
            queries = self._fallback_queries(file_diffs)

        print(f"  Query planner: {len(queries)} searches planned", file=sys.stderr)
        for i, (query, reason) in enumerate(queries):
            print(f"    [{i+1}] {query[:80]}{'...' if len(query) > 80 else ''}", file=sys.stderr)

        # Step 3: Execute queries via WarpGrep in parallel
        def _run_search(idx_query_reason):
            i, query, reason = idx_query_reason
            try:
                result = search_codebase_text(query, repo_path, self.config.morph_api_key, max_turns=4)
                if result and len(result) > 50:
                    return (i, reason, result)
            except Exception as e:
                print(f"  WarpGrep search {i+1} failed: {e}", file=sys.stderr)
            return None

        search_args = [(i, q, r) for i, (q, r) in enumerate(queries)]

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_run_search, arg): arg for arg in search_args}
            results = []
            for future in as_completed(futures):
                r = future.result()
                if r:
                    results.append(r)

        # Sort by original index
        results.sort(key=lambda x: x[0])

        context_parts = []
        for _, reason, text in results:
            context_parts.append(f"### {reason}\n{text}")

        return "\n\n".join(context_parts)

    def _fallback_queries(self, file_diffs: list[FileDiff]) -> list[tuple[str, str]]:
        """Minimal fallback queries if the planner fails."""
        import re
        changed_functions = []
        for fd in file_diffs[:10]:
            for line in fd.raw_diff.split("\n")[:200]:
                if line.startswith("+") and not line.startswith("+++"):
                    m = re.search(r'(?:def|func|function|public|private|protected)\s+(\w+)', line)
                    if m and m.group(1) not in ("__init__", "main", "test"):
                        changed_functions.append(m.group(1))
        changed_functions = list(dict.fromkeys(changed_functions))[:6]
        if changed_functions:
            func_list = ", ".join(changed_functions)
            return [(
                f"Find all callers and usages of: {func_list}",
                "Callers of changed functions",
            )]
        files_list = ", ".join(fd.file_path for fd in file_diffs[:5])
        return [(
            f"Find code that depends on these files: {files_list}",
            "Dependencies of changed files",
        )]

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

    def _pass1_review(self, file_diffs: list[FileDiff], warpgrep_context: str = "", repo_path: str | None = None) -> list[ReviewIssue]:
        """Pass 1: Comprehensive bug-finding review with pre-gathered context."""
        combined_diff = self._build_diff_text(file_diffs)

        # Gather language hints
        languages = {fd.language for fd in file_diffs}
        lang_hints = "\n\n".join(
            f"**{lang}:**\n{get_language_hint(lang)}"
            for lang in languages
            if get_language_hint(lang)
        )

        context_section = ""
        if warpgrep_context:
            context_section = f"""
## Codebase Context (from repository search)
The following code exists in the repository outside the diff. Use this to verify function signatures, understand callers, and check contracts before claiming something is undefined or broken.

{warpgrep_context}
"""

        tools_section = ""
        if repo_path:
            tools_section = """
## Available Tools
You have tools to read files and search the codebase. USE THEM AGGRESSIVELY to investigate every suspicious pattern:
- Use grep_pattern to find all callers of changed functions - check if they break with the new behavior.
- Use read_file to read the FULL context around changed code - understand what the code does before and after.
- Use warpgrep_codebase_search for broader investigations - find related code, contracts, and patterns.
- Investigate EVERY suspicious pattern you see. Deep investigation finds bugs that surface-level review misses.

IMPORTANT: Tools are for INVESTIGATION, not dismissal. If you investigate and find confirming evidence of a bug, report it with the evidence. If the diff shows a clear bug (wrong locale, typo, inverted logic), report it immediately without needing tool verification.
"""

        prompt = f"""Review this pull request for bugs and correctness issues.

## Changed Files ({len(file_diffs)} files)
{combined_diff}

{f"## Language-Specific Notes{chr(10)}{lang_hints}" if lang_hints else ""}
{context_section}{tools_section}
## What to Look For
{self._review_instructions if self._review_instructions else self._default_review_instructions()}

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

    def _pass2_review(self, file_diffs: list[FileDiff], warpgrep_context: str = "", repo_path: str | None = None) -> list[ReviewIssue]:
        """Pass 2: Different focus to catch bugs missed by first pass."""
        combined_diff = self._build_diff_text(file_diffs)

        context_section = ""
        if warpgrep_context:
            context_section = f"""
## Codebase Context (from repository search)
Use this context to verify claims about function signatures, callers, contracts, and definitions before reporting issues.

{warpgrep_context}
"""

        tools_section = ""
        if repo_path:
            tools_section = """
## Available Tools
You have tools: read_file, grep_pattern, list_directory, warpgrep_codebase_search. USE THEM AGGRESSIVELY:
- Use grep_pattern to find ALL callers/consumers of changed functions - each caller is a potential breakage point.
- Use read_file to read surrounding code, especially what happens BEFORE and AFTER the changed section.
- Use warpgrep_codebase_search for semantic queries like "how is X used across the codebase".
- Investigate every suspicious pattern. The more you investigate, the more real bugs you find.

Report clear diff-visible bugs immediately. Use tools to find ADDITIONAL bugs that require cross-file context.
"""

        prompt = f"""Review this PR diff with fresh eyes. Look for bugs that a FIRST reviewer commonly misses.

## Changed Files
{combined_diff}

{context_section}{tools_section}
## Commonly Missed Bug Patterns

Check each of these SPECIFICALLY against the diff:

1. **Behavioral regressions from removals**: Did the PR remove safety checks, filters, permission checks, or error handling that was protecting against something? Look for removed .filter(), removed if-guards, removed permission checks.

2. **Cross-file inconsistency**: Are metric tags, string constants, or key names consistent across all files? (e.g., 'shard' vs 'shards'). Do function signatures match their call sites?

3. **Concurrency — cross-reference after lock changes**: If a lock/mutex scope is reduced or removed, find ALL readers/writers of the previously-protected resource. ANY reader that accesses the resource without holding the lock is now a race condition. Don't just report the lock change; report every unsynchronized access point.

4. **Loop early termination skipping cleanup**: For any loop with break/return, verify that cleanup/finalization still executes for ALL items. If a loop breaks early, do remaining items still get properly handled (joined, closed, terminated)?

5. **Parallel iteration mismatch**: When two collections are iterated in parallel (zip, dual iterators, nested find()), verify they handle size mismatches. If collection A has more items than B, the iteration may go out of bounds.

6. **Return value changes**: Adding a new expression as the last line of a method changes its return value. In Ruby around_action, in Python __exit__, in any callback framework, this can silently break the control flow.

7. **Framework contract violations**: In Ruby, does before_validation work on nil? In Python, is a class field mutable default? In TypeScript, does === compare objects by reference? In Go, does Exec() expect (query, args...) format?

8. **Test validity**: Does a monkeypatch make the thing being tested a no-op? Does the test HTTP method match the route? Does the test name match what it tests? Do test assertions match test comments?

9. **Thread-safety of lazy initialization**: Instance variables initialized lazily (||=, ??=, or if-nil patterns) without synchronization are unsafe under concurrent access. Multiple threads may both see the uninitialized state and race.

10. **Symbol vs String confusion** (Ruby): :symbol != "string". Hash keys and include? checks can silently fail when mixing types.

11. **Security surface changes**: Any weakening of auth, frame options, origin validation, URL validation, or input sanitization?

12. **Scope/naming errors**: Is a variable referenced from the wrong scope? Is a property name misspelled? Does a method name have a typo that prevents it from being called?

Also check for:
- Method/function name typos that prevent discovery or matching
- Property name typos (misspelled identifiers)
- Dead code where results are computed then discarded
- Docstring that contradicts implementation
- Wrong log level (Error for debug info)
- Hardcoded values ignoring configuration

CRITICAL RULES:
- Do NOT claim variables/functions are "undefined" or "not imported" unless you can PROVE it from the diff. The diff only shows changed lines - definitions may exist in the file outside the diff context.
- Do NOT suggest defensive null checks ("X could be null") without a CONCRETE path where null arrives.
- Do NOT report speculative edge cases without evidence they occur.
- Do NOT report the same bug multiple times across different files. Report it ONCE.
- Quality over quantity: 3 real bugs > 8 questionable ones.

Each comment must be SELF-CONTAINED: name the specific code element, state what's wrong, and explain the runtime consequence.
Return [] if nothing found.

{{"file_path": "...", "line_number": N, "category": "logic_error|incorrect_value|api_misuse|race_condition|null_reference|type_error|security|localization|test_correctness|portability", "severity": "critical|high|medium|low", "confidence": 0.5-1.0, "comment": "Describe the specific bug."}}"""

        response_text = self._call_opus(prompt, repo_path=repo_path)
        return self._parse_issues(response_text, source_pass="pass2")

    def _pass3_review(self, file_diffs: list[FileDiff], warpgrep_context: str = "", repo_path: str | None = None) -> list[ReviewIssue]:
        """Pass 3: Deep investigation pass - uses tools aggressively to find and confirm bugs."""
        combined_diff = self._build_diff_text(file_diffs)

        context_section = ""
        if warpgrep_context:
            context_section = f"""
## Codebase Context
{warpgrep_context}
"""

        tools_section = ""
        if repo_path:
            tools_section = """
## Available Tools
You have tools: read_file, grep_pattern, list_directory, warpgrep_codebase_search.
THIS PASS REQUIRES DEEP TOOL-ASSISTED INVESTIGATION. Your goal is to find bugs that become apparent only after reading surrounding code.

Strategy:
1. For EVERY changed function: grep for all callers, then read each caller to check if it breaks
2. For EVERY removed line: read the surrounding context to understand what it was protecting
3. Use warpgrep_codebase_search to find how changed interfaces/types are used across the codebase
4. Read test files to check if tests still match the changed behavior

The MORE you investigate, the MORE bugs you will find. Each tool call is an opportunity to discover a real issue.
"""

        prompt = f"""You are a thorough code investigator. Read the diff, then USE TOOLS to investigate every suspicious change.

## Changed Files
{combined_diff}

{context_section}{tools_section}
## What to Investigate

For each suspicious pattern in the diff, use tools to confirm or deny it:

1. **Caller breakage**: Grep for all callers of changed functions. Read each caller. Do any pass wrong arguments now? Do any depend on the old return value?

2. **Removed safety**: For removed lines (- lines), read the full file context. Was the removed code a guard, a check, a permission gate? What happens without it?

3. **Contract violations**: Read the interface/abstract class. Does the changed implementation still satisfy the contract? Wrong return type? Missing case?

4. **Cross-file consistency**: Grep for string constants, enum values, metric tags. Are they consistent across producer and consumer?

5. **Behavioral regression**: Read downstream consumers. Does the behavioral change break their assumptions?

6. **All diff-visible bugs too**: If you see a clear bug in the diff (wrong locale, typo, inverted logic, wrong variable), report it. Use tools to STRENGTHEN the finding with evidence, not to dismiss it.

RULES:
- Use tools to INVESTIGATE, not to dismiss. Finding context that confirms a bug is valuable.
- Each finding should include evidence from your tool investigation when possible.
- Do NOT report speculative issues without evidence.
- Quality over quantity: report only bugs you're confident about.

Return a JSON array. Return [] if no bugs found.
{{"file_path": "...", "line_number": N, "category": "logic_error|incorrect_value|api_misuse|race_condition|null_reference|type_error|security|localization|test_correctness|portability", "severity": "critical|high|medium|low", "confidence": 0.5-1.0, "comment": "Describe the specific bug with evidence from your investigation."}}"""

        response_text = self._call_opus(prompt, repo_path=repo_path)
        return self._parse_issues(response_text, source_pass="pass3")

    def _quick_wins_review(self, file_diffs: list[FileDiff], warpgrep_context: str = "", repo_path: str | None = None) -> list[ReviewIssue]:
        """Quick-wins pass targeting commonly-missed low-severity but real issues.

        Focuses on: typos, naming bugs, doc mismatches, wrong constants,
        portability issues, and broken test names.
        """
        combined_diff = self._build_diff_text(file_diffs)

        context_section = ""
        if warpgrep_context:
            context_section = f"""
## Codebase Context
{warpgrep_context}
"""

        prompt = f"""You are a meticulous code reviewer focused on finding SMALL BUT REAL bugs that other reviewers miss.

## Changed Files
{combined_diff}

{context_section}

## What to Find (ONLY these specific patterns)

1. **Method/function name typos**: Misspelled method names that prevent test discovery (e.g., test_inalid instead of test_invalid), break interface matching, or won't be called (e.g., santizeAnchors instead of sanitizeAnchors)

2. **Property/variable name typos**: Misspelled identifiers like 'stopNotificiationsText' instead of 'stopNotificationsText'

3. **Wrong string constants**: Inconsistent metric tags ('shard' vs 'shards'), wrong cleanup aliases, wrong locale strings

4. **Locale/translation bugs**: Text in wrong language for the locale file (Italian in Lithuanian file), Traditional Chinese characters in Simplified Chinese file

5. **Docstring contradicts code**: Comment says "returns list" but method returns dict; test comment says "allow access" but map stores false

6. **Dead code**: Encoder/builder created and written to but output never used or returned

7. **Wrong log level**: Using Error level for debug/informational messages

8. **Portability issues**: BSD-only sed syntax (-i '' fails on Linux), -ms-align-items (never existed, should be -ms-flex-align)

9. **CSS value bugs**: Wrong lightness percentage in color conversion, mixing float:left with flexbox

10. **Test name/assertion mismatch**: Test named 'test_empty_array' but tests empty dict; HTTP method in test doesn't match route

11. **Hardcoded values ignoring config**: Magic numbers that should use configurable settings

12. **Redundant code after guard**: Optional chaining (?.) after a null check already guarantees non-null

## Rules
- Each issue must be PROVABLE from the diff shown
- Do NOT report issues about undefined variables (they likely exist outside the diff)
- Do NOT report defensive programming suggestions
- Focus on REAL bugs with concrete runtime consequences
- These may be low severity but they ARE real defects

Return a JSON array. Return [] if nothing found.
{{"file_path": "...", "line_number": N, "category": "incorrect_value|test_correctness|localization|portability|logic_error", "severity": "low|medium", "confidence": 0.5-1.0, "comment": "Describe the specific bug."}}"""

        response_text = self._call_opus(prompt, repo_path=repo_path)
        return self._parse_issues(response_text, source_pass="quick_wins")

    def _llm_aggregate(
        self,
        all_pass_issues: list[list[ReviewIssue]],
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
    ) -> list[ReviewIssue]:
        """LLM-based aggregation: replaces majority voting + judge in one intelligent step.

        A single Opus call sees ALL issues from ALL passes alongside the diff,
        then deduplicates, validates, and returns only confirmed real bugs with
        calibrated confidence scores.
        """
        import sys

        if not all_pass_issues:
            return []

        # Build per-pass issue lists for the LLM
        pass_sections = []
        total_issues = 0
        for pass_idx, issues in enumerate(all_pass_issues):
            if not issues:
                continue
            items = []
            for issue in issues:
                items.append({
                    "file_path": issue.file_path,
                    "line_number": issue.line_number,
                    "category": issue.category,
                    "severity": issue.severity,
                    "confidence": issue.confidence,
                    "comment": issue.comment,
                })
            pass_sections.append({
                "pass": issue.source_pass if issues else f"pass{pass_idx+1}",
                "issues": items,
            })
            total_issues += len(items)

        if total_issues == 0:
            return []

        # Build compact diff for the aggregator
        combined_diff = self._build_diff_text(file_diffs, max_chars=60000)

        passes_json = json.dumps(pass_sections, indent=2)

        prompt = f"""You are a code review aggregator. Multiple independent review passes have examined this PR.
Your job is to synthesize their findings into a single, high-quality list of REAL bugs.

## The PR Diff
{combined_diff}

## Review Findings from {len(all_pass_issues)} Independent Passes
Each pass reviewed the same diff independently with shuffled file ordering and different prompts.
Issues found by multiple passes are more likely to be real bugs.

{passes_json}

## Your Task

Analyze all {total_issues} reported issues across {len(all_pass_issues)} passes and produce a FINAL curated list.

### Step 1: Group Duplicates
Multiple passes often find the same bug with different wording. Group issues that describe the SAME underlying problem (same file + nearby lines + same concept). Count how many passes independently found each unique bug.

### Step 2: Validate Against the Diff
For each unique bug, verify it against the actual diff:
- Does the specific code element mentioned actually exist in the diff?
- Is the claimed behavior provably wrong from the code shown?
- Does it cause incorrect runtime behavior, not just a style preference?
- Is the issue in CHANGED code (+ or - lines)?

REMOVE issues matching these false positive patterns:
- "Variable X is undefined" / "function Y doesn't exist" — the diff shows only changed lines; definitions exist outside the diff
- "Should add null check" / "could be null" — speculative without a concrete null path
- "Should use X instead of Y" — style preference when current code works
- "If input is empty/null..." — speculative edge case without evidence
- "Inefficient" / "should cache" — performance suggestions, not bugs
- Duplicate of another issue already in the list

KEEP issues like these (real bugs):
- forEach with async callbacks (provable API misuse)
- Wrong method name called (copy-paste error)
- Text in wrong language for locale file
- Inverted logic / always returns same value
- sed -i '' portability bug
- Test comment contradicts assertion
- Method name typo preventing discovery

### Step 3: Calibrate Confidence
Set confidence based on cross-pass consensus AND bug severity:
- Found by 3+ passes: confidence = 0.85-0.95 (strong consensus)
- Found by 2 passes: confidence = 0.70-0.85 (moderate consensus)
- Found by 1 pass but clearly provable from diff: confidence = 0.60-0.75
- Found by 1 pass and requires inference: confidence = 0.45-0.55

### Output Format
Return a JSON array of the FINAL validated issues. Each issue should use the BEST description from any pass (most specific, most evidence-rich).

```json
[{{"file_path": "...", "line_number": N, "category": "logic_error|incorrect_value|api_misuse|race_condition|null_reference|type_error|security|localization|test_correctness|portability", "severity": "critical|high|medium|low", "confidence": 0.0-1.0, "comment": "Clear, specific description of the bug with evidence.", "pass_count": N}}]
```

Rules:
- Quality over quantity: 5 real bugs > 12 questionable ones
- Each comment must be SELF-CONTAINED: name the code element, state what's wrong, explain the consequence
- Do NOT invent new issues — only synthesize from the passes above
- When two descriptions of the same bug exist, keep the more specific/evidence-rich one
- KEEP BIAS: When in doubt, KEEP the issue. False negatives (missing a real bug) are WORSE than false positives. Only REMOVE issues you are confident are false positives.
- Single-pass findings from the deep investigation pass (pass3/pass6) may be uniquely valid — do not auto-remove just because only one pass found them
- Think hard about each issue before deciding to remove it"""

        print(f"  Aggregating {total_issues} issues from {len(all_pass_issues)} passes...", file=sys.stderr)

        # Aggregator does NOT get tools — it works purely from the diff + issues
        response_text = self._call_opus(prompt, repo_path=None)
        aggregated = self._parse_issues(response_text, source_pass="aggregated")

        print(f"  Aggregation: {total_issues} raw -> {len(aggregated)} curated", file=sys.stderr)

        return aggregated

    def _majority_vote_merge(
        self, all_pass_issues: list[list[ReviewIssue]]
    ) -> list[ReviewIssue]:
        """Merge issues from N passes using majority voting.

        Each unique issue gets a vote count (how many passes found it).
        - 3+ votes: confidence += 0.15 (high consensus)
        - 2 votes: confidence += 0.05 (moderate consensus)
        - 1 vote: confidence -= 0.15 (no consensus, likely FP)

        This is the BugBot technique: shuffled multi-pass + consensus filtering.
        """
        if not all_pass_issues:
            return []

        # Flatten all issues and track which pass they came from
        @dataclass
        class VotedIssue:
            issue: ReviewIssue
            vote_count: int = 1
            pass_indices: list = field(default_factory=list)

        # Start with issues from first pass as the canonical set
        voted: list[VotedIssue] = []
        for issue in all_pass_issues[0]:
            voted.append(VotedIssue(issue=issue, vote_count=1, pass_indices=[0]))

        # Match issues from subsequent passes
        for pass_idx in range(1, len(all_pass_issues)):
            for new_issue in all_pass_issues[pass_idx]:
                matched = False
                for vi in voted:
                    if self._issues_same(vi.issue, new_issue):
                        vi.vote_count += 1
                        vi.pass_indices.append(pass_idx)
                        # Keep the higher-confidence version
                        if new_issue.confidence > vi.issue.confidence:
                            new_issue.source_pass = vi.issue.source_pass  # preserve original pass label
                            vi.issue = new_issue
                        matched = True
                        break
                if not matched:
                    voted.append(VotedIssue(
                        issue=new_issue,
                        vote_count=1,
                        pass_indices=[pass_idx],
                    ))

        # Apply confidence adjustments based on vote count
        num_passes = len(all_pass_issues)
        result = []
        for vi in voted:
            issue = vi.issue
            if num_passes >= 4:
                if vi.vote_count >= 3:
                    issue.confidence = min(1.0, issue.confidence + 0.15)
                elif vi.vote_count == 2:
                    issue.confidence = min(1.0, issue.confidence + 0.05)
                else:  # vote_count == 1
                    issue.confidence = max(0.0, issue.confidence - 0.15)
            elif num_passes >= 2:
                if vi.vote_count >= 2:
                    issue.confidence = min(1.0, issue.confidence + 0.10)
                else:
                    issue.confidence = max(0.0, issue.confidence - 0.10)

            issue.source_pass = f"{issue.source_pass}(v{vi.vote_count})"
            result.append(issue)

        # Final cross-file deduplication: collapse same conceptual bug across files.
        # After voting, issues like "forEach with async" may appear for 3 files.
        # Keep only the highest-confidence instance of each conceptual bug.
        result = self._cross_file_dedup(result)

        return result

    def _cross_file_dedup(self, issues: list[ReviewIssue]) -> list[ReviewIssue]:
        """Collapse same conceptual bug reported across multiple files.

        E.g., "forEach with async callback" in vital/reschedule.ts,
        wipemycalother/reschedule.ts, and bookings.tsx should be kept once.
        """
        if len(issues) <= 1:
            return issues

        from difflib import SequenceMatcher

        # Sort by confidence desc so we keep the best version
        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        issues.sort(key=lambda x: (-x.confidence, severity_order.get(x.severity, 4)))

        kept: list[ReviewIssue] = []
        for issue in issues:
            is_cross_file_dup = False
            for existing in kept:
                # Only check cross-file (same-file already handled by vote merge)
                if issue.file_path == existing.file_path:
                    continue
                if issue.category != existing.category:
                    continue
                # Check text similarity
                ratio = SequenceMatcher(
                    None,
                    issue.comment.lower()[:250],
                    existing.comment.lower()[:250],
                ).ratio()
                if ratio > 0.60:
                    is_cross_file_dup = True
                    break
            if not is_cross_file_dup:
                kept.append(issue)

        return kept

    @staticmethod
    def _issues_same(a: ReviewIssue, b: ReviewIssue) -> bool:
        """Check if two issues describe the same bug (for pass merge dedup).

        Matches within same file: same category on nearby lines OR high text similarity.
        Also matches CROSS-FILE when the bug description is essentially identical
        (e.g., "forEach with async" reported in 3 different files).
        """
        same_file = a.file_path == b.file_path

        if same_file:
            nearby = (a.line_number > 0 and b.line_number > 0
                      and abs(a.line_number - b.line_number) <= 5)
            # Same file + same line + same category = same issue
            if nearby and a.category == b.category:
                return True
            # Same file + exact same line + any category = likely same issue
            # (different passes may categorize the same bug differently)
            if (a.line_number > 0 and b.line_number > 0
                    and a.line_number == b.line_number):
                return True

        # Word-level overlap check (more robust than character SequenceMatcher
        # for rephrased descriptions of the same bug)
        stop_words = {
            "the", "a", "an", "is", "in", "to", "of", "and", "or", "but",
            "for", "on", "at", "by", "with", "from", "this", "that", "it",
            "not", "be", "are", "was", "will", "can", "should", "could",
            "may", "which", "when", "if", "has", "have", "does", "do",
            "same", "also", "as", "here", "too", "so", "its", "been",
        }
        a_words = set(a.comment.lower()[:300].split()) - stop_words
        b_words = set(b.comment.lower()[:300].split()) - stop_words
        if a_words and b_words:
            overlap = len(a_words & b_words) / min(len(a_words), len(b_words))
            if same_file and overlap > 0.50:
                return True
            if not same_file and a.category == b.category and overlap > 0.55:
                return True
            if not same_file and overlap > 0.65:
                return True

        # Character-level similarity for nearly identical comments
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(
            None, a.comment.lower()[:250], b.comment.lower()[:250]
        ).ratio()
        if same_file and ratio > 0.55:
            return True
        if not same_file and ratio > 0.70:
            return True

        # Keyword-based matching: extract identifiers and technical terms
        import re
        def extract_key_terms(text: str) -> set[str]:
            terms: set[str] = set()
            lower = text.lower()
            # Backtick-quoted terms
            terms.update(re.findall(r'`([^`]+)`', lower))
            # camelCase/PascalCase identifiers (4+ chars)
            for w in re.findall(r'[a-zA-Z_]\w{3,}', text):
                if any(c.isupper() for c in w[1:]) or '_' in w:
                    terms.add(w.lower())
            return terms

        a_terms = extract_key_terms(a.comment)
        b_terms = extract_key_terms(b.comment)

        if a_terms and b_terms:
            shared = a_terms & b_terms
            # If they share 2+ technical identifiers and same category
            if len(shared) >= 2 and a.category == b.category:
                return True
            # 3+ shared identifiers regardless of category
            if len(shared) >= 3:
                return True

        return False

    def judge_issues(
        self,
        issues: list[ReviewIssue],
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
    ) -> list[ReviewIssue]:
        """Dedicated judge pass: validate each issue against the diff.

        Uses a strict, skeptical prompt with few-shot examples to filter FPs.
        Research shows this two-phase approach (detect aggressively, then validate
        strictly) eliminates 72-96% of FPs while retaining TPs.
        """
        import sys

        if not issues:
            return []

        combined_diff = self._build_diff_text(file_diffs, max_chars=60000)

        # Format issues for the judge
        issues_json = json.dumps([i.to_dict() for i in issues], indent=2)

        judge_body = self._judge_prompt if self._judge_prompt else self._default_judge_prompt()

        judge_prompt = f"""You are a code review validator. Your job is to examine each claimed bug and decide: KEEP or REMOVE.

## The PR Diff
{combined_diff}

## Claimed Issues to Validate
{issues_json}

{judge_body}

## Output

Return a JSON array containing ONLY the validated true bugs. Remove clear false positives but KEEP issues that describe real, provable bugs.
Keep the exact same format as the input.

When in doubt about whether an issue is real, KEEP it - false negatives are worse than false positives."""

        # Judge does NOT get tools - tools cause over-verification where
        # the judge reads surrounding code and talks itself out of real bugs.
        response_text = self._call_opus(judge_prompt, repo_path=None)

        # Parse validated issues
        validated = self._parse_issues(response_text, source_pass="validated")

        # Match validated issues back to originals to preserve vote metadata
        result = []
        for v_issue in validated:
            # Find the original issue this corresponds to
            best_match = None
            best_overlap = 0.0
            for orig in issues:
                if orig.file_path == v_issue.file_path and self._issues_same(orig, v_issue):
                    # Preserve original source_pass (with vote count)
                    v_issue.source_pass = orig.source_pass
                    best_match = v_issue
                    break
                # Fallback: word overlap matching
                stop = {"the", "a", "an", "is", "in", "to", "of", "and", "or"}
                o_words = set(orig.comment.lower()[:200].split()) - stop
                v_words = set(v_issue.comment.lower()[:200].split()) - stop
                if o_words and v_words:
                    overlap = len(o_words & v_words) / min(len(o_words), len(v_words))
                    if overlap > best_overlap:
                        best_overlap = overlap
                        best_match = v_issue
                        best_match.source_pass = orig.source_pass

            if best_match:
                result.append(best_match)

        removed = len(issues) - len(result)
        if removed > 0:
            print(f"  Judge removed {removed} FPs ({len(issues)} -> {len(result)})", file=sys.stderr)

        return result

    @staticmethod
    def _default_review_instructions() -> str:
        """Default review instructions when no organism override is set."""
        return """Focus ONLY on changed lines (+ and - lines). Find issues that WILL cause incorrect behavior:

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
- Do NOT claim a variable/function/import is "undefined" or "missing" unless you can PROVE it from the diff. The diff only shows changed lines - the rest of the file exists but isn't shown. If a function is called and you don't see its definition in the diff, it's almost certainly defined elsewhere.
- Do NOT claim a variable is "used before defined" unless you can prove the ordering from the diff.
- Do NOT claim a module is "not imported" unless you see the file's complete import section in the diff and it's definitely missing.
- Do NOT suggest defensive null checks ("X could be null") unless you can trace a CONCRETE path where null arrives.
- Do NOT suggest "consider using X instead of Y" when Y works correctly.
- Do NOT report speculative edge cases ("if input is empty/malformed...") without evidence they occur.
- Do NOT report the same bug multiple times across different files. If forEach+async appears in 3 files, report it ONCE for the most important file.
- When in doubt about whether something exists outside the diff, DO NOT report it.

## Rules
- Point to the EXACT line and explain WHY it's wrong
- Each comment must be SELF-CONTAINED
- Return [] if no real bugs exist
- Quality over quantity: 3 real bugs > 8 questionable ones
- Do NOT report: pure formatting preferences, defensive programming for impossible paths, general performance suggestions, "consider" suggestions"""

    @staticmethod
    def _default_judge_prompt() -> str:
        """Default judge prompt when no organism override is set."""
        return """## KEEP an issue ONLY if ALL of these are true:
1. The specific code element mentioned actually exists in the diff
2. The claimed behavior is provably wrong from the code shown
3. It causes incorrect runtime behavior, data corruption, security vulnerability, or broken tests
4. The issue is in CHANGED code (+ or - lines), not unchanged context

## REMOVE an issue if ANY of these patterns match:

**Pattern A - Hallucinated entities:**
"Variable X is undefined" / "function Y doesn't exist" / "Z is not imported" - the diff only shows CHANGED lines. Variables, functions, and imports almost certainly exist outside the diff context. REMOVE unless you see the COMPLETE import section and can confirm the import is truly missing.

**Pattern B - Defensive programming:**
"Should add null check" / "could be null" / "might throw" / "potential NPE" - unless you can trace a CONCRETE code path where null/undefined actually arrives. "Could theoretically be null" is not a bug. REMOVE.

**Pattern C - Style preferences:**
"Should use X instead of Y" / "consider using" / "would be better to" - when the current code works correctly. REMOVE.

**Pattern D - Speculative edge cases:**
"If the input is empty/null/malformed..." / "cache collision possible" / "parameter limit could be exceeded" - theoretical problems without evidence they occur. REMOVE.

**Pattern E - Performance / best practices:**
"Inefficient algorithm" / "unnecessary allocation" / "should cache" - not bugs. REMOVE.

**Pattern F - Duplicate of another issue:**
If two issues describe the SAME bug at the SAME location (or same conceptual bug across files), keep only the better-written one. REMOVE the duplicate.

## TRUE BUG examples (keep these):
- "forEach with async: callbacks aren't awaited" -> KEEP (provable API misuse)
- "sed -i '' fails on Linux" -> KEEP (concrete portability bug)
- "Italian text in Lithuanian locale file" -> KEEP (wrong language, provable)
- "recordLegacyDuration called instead of recordStorageDuration" -> KEEP (wrong method)
- "enableSqlExpressions always returns false" -> KEEP (inverted logic, provable)
- "Test comment says 'allow' but map stores false" -> KEEP (contradiction, provable)
- "Method name typo: santizeAnchors should be sanitizeAnchors" -> KEEP (real typo)

## FALSE POSITIVE examples (remove these):
- "config variable is used but never defined" -> REMOVE (defined outside diff)
- "Should validate input before database call" -> REMOVE (defensive)
- "Potential null pointer if X returns null" -> REMOVE (speculative)
- "Consider using const instead of let" -> REMOVE (style)
- "ctx is undefined in this scope" -> REMOVE (likely defined in surrounding code)
- "Missing error handling for edge case" -> REMOVE (defensive)"""

    def _build_tools(self, repo_path: str | None) -> tuple[list[dict] | None, dict | None]:
        """Build the tool list for Opus code review.

        Tools:
        1. read_file - Read a specific file from the repo (fast, local)
        2. grep_pattern - Search for regex patterns via ripgrep (fast, local)
        3. list_directory - See repo structure (fast, local)
        4. warpgrep_codebase_search - Semantic code search via Morph API
        """
        if not repo_path:
            return None, None

        tools = [
            {
                "name": "read_file",
                "description": (
                    "Read a file from the repository. Returns the file contents with line numbers. "
                    "Use this to understand the FULL context around changed code - what happens before "
                    "and after the diff. Read callers of changed functions to check if they break. "
                    "Read the complete file to understand data flow and find issues the diff alone doesn't show. "
                    "You can specify line ranges to read specific sections. "
                    "This is a fast local operation - use it aggressively."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path relative to repo root, e.g. 'src/auth/login.ts'",
                        },
                        "lines": {
                            "type": "string",
                            "description": "Optional line ranges to read, e.g. '1-50,100-120'. Omit to read entire file.",
                        },
                    },
                    "required": ["path"],
                },
                "input_examples": [
                    {"path": "src/auth/login.ts"},
                    {"path": "src/models/user.py", "lines": "1-30"},
                    {"path": "pkg/storage/index.go", "lines": "100-150,200-220"},
                ],
            },
            {
                "name": "grep_pattern",
                "description": (
                    "Search for a regex pattern across the repository using ripgrep. "
                    "Returns matching lines with file paths and line numbers. Use this to: "
                    "find all callers of a changed function (each caller could break), "
                    "check string constants for inconsistencies across files, "
                    "find related code that depends on changed behavior, "
                    "or discover additional instances of a buggy pattern. "
                    "This is your PRIMARY investigation tool - use it aggressively to find bugs that need cross-file context."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Regex pattern to search for, e.g. 'def authenticate', 'import.*UserService', 'TODO|FIXME'",
                        },
                        "sub_dir": {
                            "type": "string",
                            "description": "Subdirectory to search in, e.g. 'src/auth'. Defaults to entire repo.",
                        },
                        "glob": {
                            "type": "string",
                            "description": "File glob filter, e.g. '*.py', '*.ts', '*.go'. Defaults to all files.",
                        },
                    },
                    "required": ["pattern"],
                },
                "input_examples": [
                    {"pattern": "sanitizeAnchors"},
                    {"pattern": "def process_shard", "glob": "*.py"},
                    {"pattern": "shards|shard_count", "sub_dir": "src/metrics"},
                ],
            },
            {
                "name": "list_directory",
                "description": (
                    "List the directory structure of a path in the repository. "
                    "Shows files and subdirectories up to 3 levels deep. "
                    "Use this to understand the project layout, find test directories, "
                    "or locate related files."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path relative to repo root, e.g. 'src/auth'. Use '.' for repo root.",
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Optional regex to filter results, e.g. 'test|spec' to find test files.",
                        },
                    },
                    "required": ["path"],
                },
                "input_examples": [
                    {"path": ".", "pattern": "test|spec"},
                    {"path": "src/auth"},
                ],
            },
        ]

        warpgrep_tool_def = None
        if self.config.morph_api_key and self.config.warpgrep_tool_enabled:
            warpgrep_tool_def = create_warpgrep_tool(repo_path, self.config.morph_api_key)
            tools.append({
                "name": warpgrep_tool_def["name"],
                "description": warpgrep_tool_def["description"],
                "input_schema": warpgrep_tool_def["input_schema"],
            })

        return tools, warpgrep_tool_def

    def _execute_tool(self, tool_block, repo_path: str, warpgrep_tool_def: dict | None) -> dict:
        """Execute a single tool call and return the tool_result."""
        import sys
        from pr_review_agent.warpgrep.client import (
            _execute_grep, _execute_read, _execute_list_directory,
        )

        name = tool_block.name
        inp = tool_block.input

        if name == "read_file":
            result_text = _execute_read(repo_path, inp.get("path", ""), inp.get("lines"))
            return {
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": result_text,
            }

        elif name == "grep_pattern":
            result_text = _execute_grep(
                repo_path,
                inp.get("pattern", ""),
                inp.get("sub_dir", "."),
                inp.get("glob"),
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": result_text,
            }

        elif name == "list_directory":
            result_text = _execute_list_directory(
                repo_path,
                inp.get("path", "."),
                inp.get("pattern"),
            )
            return {
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": result_text,
            }

        elif name == WARPGREP_TOOL_NAME and warpgrep_tool_def:
            query = inp.get("search_string", "")
            print(f"    WarpGrep: {query[:80]}", file=sys.stderr)
            result_text = execute_warpgrep_tool(inp, warpgrep_tool_def)
            return {
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": result_text if result_text else "No results found.",
            }

        else:
            return {
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": f"Unknown tool: {name}",
                "is_error": True,
            }

    def _call_opus(self, prompt: str, thinking_budget: int = 10000, repo_path: str | None = None, timeout: int = 300) -> str:
        """Call Claude with extended thinking, optionally with code review tools.

        If repo_path is provided, Claude gets tools for code review:
        - read_file: Read specific files from the repo
        - grep_pattern: Search for patterns via ripgrep
        - list_directory: Browse repo structure
        - warpgrep_codebase_search: Semantic code search via Morph API
        """
        import sys

        tools, warpgrep_tool_def = self._build_tools(repo_path)

        for attempt in range(2):
            messages = [{"role": "user", "content": prompt}]

            try:
                # If we have tools, use a non-streaming agentic loop
                if tools:
                    return self._agentic_loop(messages, tools, repo_path, warpgrep_tool_def, thinking_budget)

                # Use streaming with get_final_message for reliability
                with self.client.messages.stream(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    thinking={"type": "adaptive"},
                    temperature=1,
                    system=self.active_system_prompt,
                    messages=messages,
                ) as stream:
                    response = stream.get_final_message()

                # Extract text from response
                text_parts = []
                for block in response.content:
                    if block.type == "text":
                        text_parts.append(block.text)

                result = "\n".join(text_parts)
                if result.strip():
                    return result

                if attempt == 0:
                    print(f"  WARNING: Empty response (stop={response.stop_reason}), retrying...", file=sys.stderr)
                    continue
                return "[]"

            except Exception as e:
                if attempt == 0:
                    print(f"  API error: {e}, retrying...", file=sys.stderr)
                    continue
                print(f"  API error on retry: {e}", file=sys.stderr)
                return "[]"

        return "[]"

    def _agentic_loop(
        self,
        messages: list[dict],
        tools: list[dict],
        repo_path: str,
        warpgrep_tool_def: dict | None,
        thinking_budget: int,
        max_tool_rounds: int = 25,
    ) -> str:
        """Run Claude with code review tools in an agentic loop.

        Opus can call any tool (read_file, grep_pattern, list_directory,
        warpgrep_codebase_search) as many times as it needs. When it stops
        calling tools, we return the text if it contains JSON, otherwise
        make one final call without tools to force JSON output.
        """
        import sys

        tool_counts: dict[str, int] = {}

        for round_num in range(max_tool_rounds):
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                thinking={"type": "adaptive"},
                temperature=1,
                system=self.active_system_prompt,
                tools=tools,
                messages=messages,
            )

            # Collect text and tool blocks
            text_parts = []
            tool_use_blocks = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_use_blocks.append(block)

            # If no tool calls, Claude is done
            if not tool_use_blocks:
                result = "\n".join(text_parts)
                if result.strip() and "[" in result:
                    summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
                    print(f"  Review complete (tools: {summary or 'none'})", file=sys.stderr)
                    return result
                # No JSON in response, need final call without tools
                break

            # Execute all tool calls in parallel
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tool_block in tool_use_blocks:
                tool_counts[tool_block.name] = tool_counts.get(tool_block.name, 0) + 1
                result = self._execute_tool(tool_block, repo_path, warpgrep_tool_def)
                tool_results.append(result)
            messages.append({"role": "user", "content": tool_results})

        # Final call WITHOUT tools to force JSON output
        summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
        print(f"  Final review call (tools used: {summary or 'none'})", file=sys.stderr)
        final_response = self.client.messages.create(
            model=self.config.model,
            max_tokens=self.config.max_tokens,
            thinking={"type": "adaptive"},
            temperature=1,
            system=self.active_system_prompt,
            messages=messages,
        )

        text_parts = []
        for block in final_response.content:
            if block.type == "text":
                text_parts.append(block.text)

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
