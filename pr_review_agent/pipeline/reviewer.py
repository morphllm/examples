"""Opus 4.6 agentic code reviewer.

Single agentic loop: the model investigates the codebase (WarpGrep 8+ times),
reviews the diff, and writes findings with inline <issue> XML tags — all in
one continuous flow. Issues are parsed directly from the model's output.
"""

from __future__ import annotations

import json
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from pr_review_agent.config import Config
from pr_review_agent.pipeline.diff_parser import FileDiff
from pr_review_agent.pipeline.providers import (
    LLMProvider,
    LLMResponse,
    ToolCall as ProviderToolCall,
    create_provider,
)
from pr_review_agent.prompts.review import get_language_hint
from pr_review_agent.prompts.system import SYSTEM_PROMPT
from pr_review_agent.warpgrep.client import (
    create_warpgrep_tool,
    execute_warpgrep_tool,
    WARPGREP_TOOL_NAME,
)

# Telemetry callback type: (event_name, event_data) -> None
TelemetryCallback = Callable[[str, dict], None]


@dataclass
class ReviewMetrics:
    """Metrics collected during a single review run."""
    tool_counts: dict[str, int] = field(default_factory=dict)
    api_calls: int = 0
    api_calls_review: int = 0
    api_calls_extract: int = 0
    tool_rounds: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


def _strict_schema(schema: dict) -> dict:
    """Add additionalProperties: false to all object types in a JSON schema."""
    schema = schema.copy()
    if schema.get("type") == "object":
        schema["additionalProperties"] = False
    for key in ("$defs", "definitions"):
        if key in schema:
            schema[key] = {
                name: _strict_schema(defn) for name, defn in schema[key].items()
            }
    if "properties" in schema:
        schema["properties"] = {
            name: _strict_schema(prop) for name, prop in schema["properties"].items()
        }
    if "items" in schema and isinstance(schema["items"], dict):
        schema["items"] = _strict_schema(schema["items"])
    for key in ("minItems", "maxItems"):
        schema.pop(key, None)
    return schema


# ---------- Pydantic models for structured extraction ----------

class ReviewIssueSchema(BaseModel):
    file_path: str = Field(description="Path to the file containing the issue")
    line_number: int = Field(description="Line number where the issue occurs")
    category: str = Field(description="Category: logic_error|incorrect_value|api_misuse|race_condition|null_reference|type_error|security|localization|test_correctness|portability|style|documentation|missing_validation|refactor")
    severity: str = Field(description="Issue severity: critical|high|medium|low")
    confidence: float = Field(description="Confidence score from 0.0 to 1.0")
    comment: str = Field(description="Description of the bug: what code is wrong, what it should be, and the consequence")


class ReviewResult(BaseModel):
    issues: list[ReviewIssueSchema] = Field(description="List of real bugs found")


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
    """End-to-end agentic code reviewer using Opus."""

    def __init__(self, config: Config, on_event: TelemetryCallback | None = None):
        self.config = config
        self._on_event = on_event
        self.provider: LLMProvider = create_provider(config)
        # Prompt overrides (set via configure_from_organism)
        self._system_prompt: str | None = None
        self._review_instructions: str | None = None
        self._num_passes: int | None = None

    def _emit(self, event_name: str, data: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event_name, data)
        except Exception:
            pass

    def configure_from_organism(self, organism) -> None:
        """Override prompts with evolved values from a CodeReviewOrganism."""
        self._system_prompt = organism.system_prompt
        self._review_instructions = organism.review_instructions
        self._num_passes = organism.num_passes

    @property
    def active_system_prompt(self) -> str:
        base = self._system_prompt if self._system_prompt is not None else SYSTEM_PROMPT
        if self.config.personality:
            base += (
                "\n\n## Reviewer Persona\n"
                "You are reviewing as a specific developer's twin. "
                "Apply their review style, priorities, and focus areas:\n\n"
                f"{self.config.personality}"
            )
        return base

    @staticmethod
    def _parse_structured_issues(text: str, source_pass: str) -> list[ReviewIssue]:
        """Parse structured JSON output into ReviewIssue list."""
        try:
            parsed = json.loads(text)
            result = ReviewResult(**parsed)
            return [
                ReviewIssue(
                    file_path=i.file_path,
                    line_number=i.line_number,
                    category=i.category,
                    severity=i.severity,
                    confidence=i.confidence,
                    comment=i.comment,
                    source_pass=source_pass,
                )
                for i in result.issues
            ]
        except Exception:
            return []

    # ---------- Main entry point ----------

    def review_pr(
        self,
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
        num_passes: int | None = None,
    ) -> list[ReviewIssue]:
        """Single-loop agentic review.

        The model investigates the codebase (WarpGrep 8+ times first),
        then reviews the diff and writes findings with inline <issue> XML
        tags. Issues are parsed directly from accumulated text output.
        """
        import sys

        self._metrics = ReviewMetrics()

        combined_diff = self._build_diff_text(file_diffs)

        # Gather language hints
        languages = {fd.language for fd in file_diffs}
        lang_hints = "\n".join(
            f"- **{lang}**: {get_language_hint(lang)}"
            for lang in languages
            if get_language_hint(lang)
        )

        tools, warpgrep_tool_def = self._build_tools(repo_path)

        prompt = f"""You are a senior engineer reviewing this pull request for bugs.

## PR Diff
{combined_diff}

{f"## Language Notes{chr(10)}{lang_hints}" if lang_hints else ""}

## How to Review

**Step 1: Understand intent, then investigate.** Before looking for bugs, understand what this PR is trying to accomplish. Read the diff holistically and form a one-sentence hypothesis: "This PR does X by changing Y." This is your specification. Bugs are gaps between what the code intends and what it actually does — you can't find gaps without knowing the intent.

Next, identify the CORE of the change — the central logic that everything else supports. Start your investigation there, then trace outward along data flow and call chains. Classify changed files as: DATA (models, migrations, DB operations), API (endpoints, handlers, serializers), LOGIC (business logic, algorithms), TEST (test files), or UI (components, templates, styling). Investigate DATA and API files first.

Use `codebase_search` to understand codebase context. Make at least 8 searches targeting your uncertainties — more if the PR touches multiple subsystems.

How to search effectively with WarpGrep — it is a search AGENT, not a grep tool. Ask it conceptual QUESTIONS about behavior, architecture, and invariants:
- GOOD: "How does the caching layer invalidate entries when the underlying data changes?"
- GOOD: "What concurrency model protects [shared state]? Are there locks, channels, or transactions?"
- GOOD: "What happens to in-flight requests when [changed component] restarts or fails over?"
- GOOD: "How does the error handling contract work between [service A] and [service B]?"
- GOOD: "What invariants does [module] maintain, and could this PR break any of them?"
- BAD: "[ClassName] constructor and [field] property" — this is a keyword lookup, use `grep` instead
- BAD: "[functionName] function and its callers in [file]" — use `grep` for exact symbol lookups
Search for each major area of the diff separately. If the PR touches 3 subsystems, do at least one search per subsystem.
Make your searches HYPOTHESIS-DRIVEN, not exploratory. Before each search, form a specific theory about what could go wrong: "If this function's return type changed, callers that destructure the old shape will crash." Then search to CONFIRM or DENY that theory. Exploratory searches ("how does module X work?") give you context; hypothesis-driven searches ("do any callers of X assume it returns a list instead of a generator?") find bugs.

**Step 1.5: Surface scan.** Before deep investigation, read through EVERY changed line in the diff right now and check for these concrete, verifiable errors. Report any you find immediately with `<issue>` tags:
- **Typos** in new/changed identifiers: method names, variable names, class names, string keys. Read each identifier character-by-character. Common: transposed letters, missing letters (e.g., "santize" vs "sanitize").
- **Missing imports/definitions**: If the diff adds a new import, class reference, or callback registration, grep the codebase NOW to verify it exists. Do NOT assume it exists just because the diff references it. Non-existent imports crash at load time — this is often the most critical bug in a PR.
- **Wrong variable**: Two failure modes. (1) In similar/repeated code: when 3+ similar lines appear together (null checks, filter predicates, metric tags), read EACH line independently — is the variable correct for THAT line? (2) In transform chains: when a value is computed from an input and stored in a new variable (`normalizedX = normalize(x)`), verify all subsequent code uses the transformed variable, not the original.
- **Inconsistent naming**: Related identifiers that should match but differ (e.g., "shard" in one place, "shards" in another; "error_type" vs "error_kind"). These cause silent lookup failures.

**API CONTRACT EXCEPTION:** For changes to public interfaces (renamed endpoints, changed parameter types, added nullable fields in response DTOs, changed error codes), you do NOT need in-repo callers to report a bug. The API IS the contract — if the diff renames an endpoint from `/reviews` to `/evaluations`, that's a breaking change for external consumers even if no in-repo code references the old path. Report with confidence 0.6-0.8 based on how likely external callers exist.

**Step 2: Investigate every changed file by tracing data flow.** Don't stop after finding one bug. Budget your investigation across ALL changed files. For every non-trivial change, trace the actual data: where does the input come from, how is it transformed, and where does it end up? Bugs live where data crosses boundaries — function calls, type conversions, serialization, storage. Follow the value, not the control flow.
- Search for callers. Will they handle the new behavior correctly?
- If a function signature, interface, or return type changed, grep for ALL callers and implementers. Don't assume they were all updated.
- If a constant or key name changed, find where the old value was referenced. Was everything updated?
- If concurrency is involved (locks, goroutines, threads, async), find ALL readers and writers of the shared state. Ask: "What happens if two requests do this simultaneously?" Look specifically for: lock scope reductions (lock used to cover more code, now covers less), non-atomic read-modify-write (reading a value, computing a new one, writing it back without synchronization), iterating a shared collection while another goroutine/thread modifies it. When a PR changes the initialization order of components, ALSO trace into each component's internal code to check for internal races — the new init order may expose pre-existing concurrent access bugs in the component's shared state.
- If initialization timing changes (lazy→eager, sync→async, or vice versa), check: can multiple callers now trigger initialization simultaneously? Does the new path clean up resources on failure?
- If code branches on environment variables or feature flags, analyze BOTH paths. Bugs hide in less-tested paths.
- If a framework API is used, verify its behavior with the specific arguments used. Edge cases like empty objects, nil values, or platform differences are where bugs hide.
- If test files are changed, check: are any mocked/patched functions the same ones the test relies on for correctness? A test that patches `time.sleep` then uses `sleep()` for synchronization is broken.
- If error response format or exception types changed, grep for all callers that parse errors from this function/endpoint. Do their catch blocks match the new format?
- After checking the happy path, also trace the ERROR/FAILURE path. What happens when the operation fails, returns null, throws, or times out? Does the code still update state unconditionally (e.g., writing to cache before checking if the fetch succeeded)?
- COMPARE BOTH SIDES: When you understand one direction of a data flow (write path, grant path, insert path), explicitly check the complementary direction (read path, denial path, query path). Bugs hide in asymmetry: data stored in one format but queried in another, cache trusted for grants but not denials, SQL `lower()` on one side but not the other. Ask: "Is the reverse path consistent?"
- VERIFY NEW DEFINITIONS: If the diff imports a new class/function or registers a lifecycle callback/hook, grep to confirm the referenced symbol actually exists. Missing imports and undefined callbacks crash immediately.
- CHECK SPELLING of new identifiers — typos in method names cause NoMethodError.
- FOLLOW THE SURPRISE: If during investigation you encounter something unexpected — a function that does more than its name suggests, a type that's different than expected, a return value that doesn't match the declared type, a variable that shadows another — STOP and investigate it immediately. Do not note it and move on. The surprise IS the bug lead. Form a hypothesis about what could go wrong because of it, then search to confirm or deny.
- TRACE WITH EDGE CASES: After reading a function, pick a concrete edge case input (nil, empty collection, count mismatch, deadline expiry, zero-length) and mentally execute the code path step by step. Don't just read the structure — simulate what happens. Especially for: loops with break/deadline conditions ("what items are skipped when the break fires?"), validation callbacks ("what if the input is nil?"), and data migrations ("does the inserted format match how the app queries it later?").
- TRACE BACKWARD FROM SIDE EFFECTS: When the diff writes to a database, updates a cache, sends a notification, or emits an event, trace BACKWARD: "what conditions must be true for this write to be correct?" Then verify those conditions are actually checked before the write happens. The model naturally traces forward (input→transform→output) but bugs often hide in missing precondition checks before state mutations.
- CHECK INVARIANTS: When the diff changes a data structure, type definition, or interface, ask: "what implicit contracts did callers rely on?" Then grep for callers and verify they still work. For example: if a field changes from required to optional, do consumers handle the null case? If a return type changes from `T` to `T | null`, do callers check for null?

INVESTIGATION PRIORITY: Spend most of your tool budget on data handling, API/backend logic, migrations, and concurrency code. These are where critical bugs hide. Then check UI/component files for concrete issues (wrong values, broken responsive design, inconsistent idioms). If one UI component is swapped for another with slightly different default styling, that is a style choice, not a bug.
LARGE PR RULE: If the PR has >30 changed files or >500 added lines, you MUST investigate backend/API/data files BEFORE frontend/UI files. Report at most 2 issues from frontend visualization or component files. Backend data bugs, API contract violations, and test correctness bugs are far more valuable than UI rendering opinions.

**Step 3: Write your review.** For each confirmed bug, output an `<issue>` XML tag with the details. Every issue must be backed by evidence from your tool calls.

Use this EXACT format for each bug you find:

<issue>
<file_path>path/to/file.ext</file_path>
<line_number>42</line_number>
<category>logic_error</category>
<severity>high</severity>
<confidence>0.85</confidence>
<comment>Description of the bug: what code is wrong, what it should be, and the runtime consequence. Cite code as evidence.</comment>
</issue>

Valid categories: logic_error, incorrect_value, api_misuse, race_condition, null_reference, type_error, security, localization, test_correctness, portability, style, documentation, missing_validation, refactor
Valid severities: critical, high, medium, low
Confidence: 0.5-1.0 (0.9+ = certain, 0.7-0.89 = very likely, 0.5-0.69 = probable)

WRITING GOOD BUG DESCRIPTIONS: Each issue comment MUST include these three parts:
1. **Quote the exact code** that's wrong (the specific expression, variable, or method call from the diff)
2. **State what it should be** (the correct value, type, or behavior)
3. **Describe the runtime consequence** (what breaks, crashes, or produces wrong results when executed)

You can output `<issue>` tags at ANY point during your investigation — as soon as you confirm a bug, report it. Don't wait until the end.

Important investigation rules:
- Don't stop at the first finding. Keep investigating ALL changed files.
- STRICT: Report each unique root cause ONCE. If the same bug pattern appears in multiple functions or files, write ONE issue and list all affected locations in the comment. Two candidates for the same root cause = wasted budget. Before writing a new issue, check if you already reported the same underlying problem. If you have 3+ findings from one file, critically evaluate whether they are truly independent or different facets of the same concern.
- Before claiming "X doesn't exist" or "Y is not imported", search the entire repo. Definitions exist outside the diff.
- Don't over-extrapolate edge cases. Only report bugs you can demonstrate via actual code paths, not theoretical "what if" scenarios.
- Check for naming bugs: method name typos, property name typos, error messages that contradict the operation.
- Do NOT report CSS/styling property differences (padding, margin, align, gap, text-overflow, overflow, color values, height) as bugs when one styled component replaces another. Different default styling is an intentional choice, not a bug. Only report if it causes a runtime crash or if values are clearly broken (e.g., negative dimensions, z-index conflicts that hide content).
- Do NOT report code organization issues (wrong file names, wrong file placement) when the framework loads by class/function name, not by filename.

BUDGET YOUR INVESTIGATION: You have a limited number of tool rounds. Do NOT spend more than 2-3 searches on the same question. If a symbol, class, or file doesn't appear after 2 searches, it either doesn't exist or isn't relevant — move on. Spread your investigation across ALL areas of the diff rather than deep-diving into one area. A common failure mode is spending 15+ rounds chasing one question while ignoring the rest of the PR.

FREQUENTLY MISSED PATTERNS — check each one explicitly:
1. CONCURRENCY: If lock scope was REDUCED (lock used to cover reads+writes, now only covers writes), trace what happens when thread A reads the unlocked data while thread B writes. If a shared map/cache is iterated by one goroutine while another modifies it, that's a crash. Not just "could race" — trace the specific interleaving. When you find one concurrency issue, keep looking — PRs with concurrency changes often have MULTIPLE independent race conditions on different shared state.
2. NEAR-DUPLICATE LINES: When multiple similar statements appear together (null checks, validation calls, string literals, filter predicates, metric tags), verify EACH ONE independently. The differentiating detail — the variable name, string key, filter condition, or scope — is where bugs hide. Read each line as if the others don't exist. Common patterns: wrong variable in a null/validation check, wrong filter predicate in a bulk delete/update (handler for type A accidentally filters on type B), inconsistent string literals that should match (metric tags, error codes, config keys).
3. TEST MOCKING: If a test patches/mocks a function (time.sleep, network calls), verify the test doesn't DEPEND on that function for its correctness. Patching sleep then using sleep for timing = broken test.
4. DATA FORMAT MISMATCH: If PR adds a DB migration or bulk insert, verify the stored data format matches how the application queries it. Data stored in one representation but queried in another (different normalization, different casing, different structure) causes silent lookup failures. Focus on data correctness, not just SQL injection.
5. COLLECTION ORDERING: When zip(), positional indexing, or iteration order is used with results from a batch/cache lookup, verify ordering is guaranteed. Results from batch fetches, caches, or database multi-gets may return in arbitrary order — iterating `.values()` and zipping with input keys can silently pair the wrong items.
6. API CONTRACTS: If error response format, exception types, or return type changes, grep for ALL callers/catch blocks that parse the OLD format. Breaking change in error shape = runtime crash in consumers.
7. NAMING: Spell-check new/changed identifiers. Typos in method names cause NoMethodError/AttributeError, typos in string keys cause silent lookup failures, typos in test method names prevent test discovery. Report immediately.
8. ORM/DB WRITES: If update/save is called with empty or partial data object, check if auto-managed timestamp fields get skipped.
9. MISSING DEFINITIONS: If the diff imports a new class/function, grep the source module to verify it actually exists. If the diff registers a callback or lifecycle hook, grep to verify the callback method is defined on the target class. Missing imports and undefined callbacks cause immediate crashes.
10. QUERY NORMALIZATION: If code queries data with normalization (lower(), strip(), downcase()), verify the stored data was inserted with the same normalization. Check both direct inserts AND migrations for consistency with how the application reads the data. In migrations that copy data from old settings/tables via raw SQL, the old data is often NOT normalized.
11. PLATFORM PORTABILITY: If the PR adds or modifies shell commands, scripts, or CLI invocations, check for OS-specific syntax (e.g., sed -i flag differs between macOS and Linux, date format specifiers vary, find flags differ). Scripts that work on one OS may fail silently or crash on another.
12. CRUD COMPLETENESS: If new controller actions or API endpoints are added, verify all CRUD operations have proper validation and authorization. A new "create" may validate inputs, but does the corresponding "update" also validate? Does "destroy" check authorization?
13. ASYNC/SYNC CONTRACT: If a previously synchronous function call was changed to asynchronous (or vice versa), check ALL callers. In JS/Ember, a function that now returns a Promise instead of a direct value will break callers that read properties synchronously. In Python, a missing `await` returns a coroutine object instead of the actual value. In Ruby, a method that now uses callbacks/blocks where it used to return directly. Missing async handling = race condition or undefined data.
14. ERROR PATH HANDLING: After checking the success path, trace the error/failure path. If code updates state (cache, counter, DB record) and THEN performs a fallible operation, what happens on failure? If the fallible operation is performed first and the result is stored unconditionally (even on error), the error gets cached. Look for: unconditional state assignments after try/catch, cache writes that don't check error status, cleanup that runs only on success but should also run on failure.
15. TEST STATE ISOLATION: When tests use shared mutable state (caches, singletons, registries, class-level variables, module globals), verify each test resets that state. Tests that pass alone but fail together indicate missing cleanup in setUp/beforeEach/afterEach. Shared cache instances, connection pools, or registries that accumulate entries across tests are common sources.
IMPORTANT: Report every bug you find that you believe is real. It is better to report a borderline bug than to miss a real one. Even if you only have moderate confidence (0.5-0.7), report it — the confidence score communicates your uncertainty. Do NOT hold back findings.

FOLLOW THROUGH ON FINDINGS: If during your investigation you discover something suspicious (a misspelling, an unexpected type, a missing method), you MUST either: (a) report it as a bug with an <issue> tag, or (b) explicitly write "NOT A BUG because [specific reason]". Do NOT silently move on. Common findings you must follow through on:
- A method/variable name that is misspelled → report as naming bug immediately
- A monkeypatch/mock that disables the mechanism the test relies on → report as test bug
- A loop with early exit that skips cleanup of remaining items → report as logic error
- A cache that trusts stale data for one outcome but not the other → report as race condition

BEFORE FINISHING: Scan through ALL changed files one more time. For any file you haven't investigated, do a quick check. It's easy to spend all your time on the first few files and miss bugs in the rest. Additionally, check these high-value patterns one more time:
- DELEGATION: If any changed class wraps/proxies/caches another object, verify method calls go to the delegate, not `self`. Self-calls through caching layers = infinite recursion.
- ERROR PATH: For any code that stores results (cache, DB, state), trace what happens if the operation FAILS. Does it cache errors? Does it update state before knowing the operation succeeded?
- NULL ACCESS: For any chained property access on data from external sources (API responses, DB queries, configs), verify intermediate keys exist before accessing nested values.

FINAL SELF-CRITIQUE: After completing your review, re-read each <issue> you reported and apply these filters. DROP any finding where:
- Your evidence is "this COULD fail if..." rather than "this DOES fail because..."
- You assumed how a framework API behaves (e.g., what happens with empty input, nil, edge cases) WITHOUT verifying via grep/search. If you didn't grep for it, you don't know.
- The bug requires multiple unlikely conditions to align simultaneously
- You're reporting behavior gated by a feature flag that looks intentionally minimal or scaffolded (WIP feature)
- Your finding is about code ORGANIZATION (file naming, module structure) rather than runtime behavior
- You're speculating about what a framework "might" do internally rather than citing actual code paths
- You cannot describe a CONCRETE input/scenario that triggers the bug. For every issue, you must be able to say: "When [specific trigger] happens, [specific code] executes and produces [specific wrong result]". If you can only say "this looks wrong" or "this might cause issues", drop it.
- You're reporting that a feature/parameter was REMOVED or CHANGED when it could be an intentional simplification. Unless the removed feature is still referenced by callers (verify via grep), it's not a bug.
- You're reporting a DESIGN CONCERN rather than a BUG. "This should use a queue" or "This should persist state" or "Consider adding retry logic" are architectural suggestions. Unless the current code produces wrong results on a concrete input, it's not a bug.

THREE-QUESTION FILTER — apply to EVERY finding before keeping it:
Q1. IS IT A NITPICK? If the fix is "rename this" or "reformat this" and the code works correctly as-is, drop it. But missing validation, wrong variables, and broken rendering are NOT nitpicks.
Q2. DID I VERIFY OR ASSUME? If your reasoning depends on how a framework/library/API behaves and you didn't grep or search to confirm, you're guessing. Either verify now or drop it.
Q3. WOULD THE AUTHOR SAY "GOOD CATCH"? The best findings make the developer immediately recognize the problem. If they'd say "that's a fair point but we'll track it separately" rather than "good catch, fixing now", lower confidence to 0.5 or drop it.

KEEP findings where you have CONCRETE evidence: wrong variable name, wrong type, wrong string literal, missing null check, wrong operator, verified-missing definition, confirmed wrong behavior via grep."""

        if tools:
            messages = [{"role": "user", "content": prompt}]
            changed_files = [fd.file_path for fd in file_diffs]
            try:
                review_text, trace = self._agentic_loop(
                    messages, tools, repo_path, warpgrep_tool_def,
                    thinking_budget=12000, max_tool_rounds=35,
                    changed_files=changed_files,
                )
            except Exception as e:
                print(f"  Review failed: {e}", file=sys.stderr)
                return []
        else:
            review_text = self._call_opus(prompt)
            trace = []

        # Store trace and full review text for debugging
        self._last_trace = trace
        self._last_review_text = review_text

        # Parse <issue> XML tags directly from the model's output
        all_issues = self._parse_xml_issues(review_text)

        # Post-processing dedup: merge issues with same category and overlapping descriptions
        all_issues = self._dedup_issues(all_issues)

        print(f"  Review complete: {len(all_issues)} issues", file=sys.stderr)
        self.last_metrics = self._metrics
        return all_issues

    def _surface_scan(
        self,
        combined_diff: str,
        tools: list[dict],
        repo_path: str | None,
        warpgrep_tool_def: dict | None,
    ) -> list[ReviewIssue]:
        """Short focused pass looking for surface-level bugs the main review may have missed.

        Runs a separate, independent conversation with a concise prompt targeting
        concrete verifiable errors: typos, wrong variables, missing imports,
        inconsistent names, copy-paste mistakes.
        """
        surface_prompt = f"""Check this PR diff for surface-level errors that are IMMEDIATELY visible in the code text.

## PR Diff
{combined_diff}

## Rules
- ONLY report bugs you can verify by reading the diff text or by a single grep.
- Do NOT report logic analysis, framework behavior, or speculative issues.
- Do NOT re-report issues you'd expect a thorough code reviewer already found.

## Check these 4 patterns:

1. **TYPOS**: Misspelled identifiers — read each new method/variable/class name letter-by-letter.
2. **WRONG VARIABLE**: A null check, assertion, or conditional that tests the wrong variable for its context.
3. **INCONSISTENT NAMES**: String keys, metric tags, or enum values that should match but differ (e.g., "shard" vs "shards").
4. **MISSING DEFINITIONS**: New imports or callback registrations where the target doesn't exist. Grep to verify.

Report with <issue> tags. Only report if confidence >= 0.80. If nothing found, say "No issues."

Valid categories: logic_error, incorrect_value, type_error, null_reference, localization, test_correctness"""

        messages = [{"role": "user", "content": surface_prompt}]
        try:
            scan_text, _ = self._agentic_loop(
                messages, tools, repo_path, warpgrep_tool_def,
                thinking_budget=3000, max_tool_rounds=5,
            )
        except Exception:
            return []

        issues = self._parse_xml_issues(scan_text)
        # Tag as surface scan findings
        for i in issues:
            i.source_pass = "surface"
        return issues

    def _extract_issues(self, review_text: str, diff_text: str) -> list[ReviewIssue]:
        """Extract structured issues from freeform review text using structured JSON output."""
        extraction_prompt = f"""Extract the bugs from this code review into structured JSON format.

<review>
{review_text[:30000]}
</review>

For each bug the reviewer identified, output an issue object. Only extract issues the reviewer explicitly identified as bugs. Do not invent new ones. If the review found no bugs, output an empty issues list.

Each issue must have these fields:
- file_path: path to the file (string)
- line_number: line number where the issue occurs (integer)
- category: one of logic_error, incorrect_value, api_misuse, race_condition, null_reference, type_error, security, localization, test_correctness, portability, style, documentation, missing_validation, refactor
- severity: one of critical, high, medium, low
- confidence: 0.0-1.0 based on how certain the reviewer was (number)
- comment: description of the bug — what code is wrong, what it should be, and the runtime consequence (string)"""

        schema = _strict_schema(ReviewResult.model_json_schema())

        try:
            t_extract = time.monotonic()
            response = self.provider.extract_json(
                messages=[{"role": "user", "content": extraction_prompt}],
                json_schema=schema,
                max_tokens=8000,
                model=self.config.model,
            )
            extract_duration = round(time.monotonic() - t_extract, 2)
            if hasattr(self, '_metrics'):
                self._metrics.api_calls += 1
                self._metrics.api_calls_extract += 1
                self._metrics.total_input_tokens += response.usage.input_tokens
                self._metrics.total_output_tokens += response.usage.output_tokens
            text = response.text_parts[0] if response.text_parts else ""
            issues = self._parse_structured_issues(text, source_pass="review")
            self._emit("review.extraction", {
                "review_text_length": len(review_text),
                "review_text_preview": review_text[:2000],
                "issues_extracted": len(issues),
                "duration_s": extract_duration,
                "success": True,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "extraction_response_text": text[:2000],
                "extraction_response_text_length": len(text),
            })
            return issues
        except Exception as exc:
            print(f"  Extraction failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            self._emit("review.extraction_error", {
                "review_text_length": len(review_text),
                "review_text_preview": review_text[:500],
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
            return []

    @staticmethod
    def _parse_xml_issues(text: str) -> list[ReviewIssue]:
        """Parse <issue> XML tags from the model's output text.

        Robust to:
        - Tags split across multiple lines
        - Extra whitespace around tag contents
        - Missing optional fields (defaults applied)
        - Nested code snippets containing < or > characters
        - Greedy comment content with XML-like fragments
        """
        import re
        issues = []

        # Match <issue>...</issue> blocks, allowing nested content
        for match in re.finditer(r"<issue\b[^>]*>(.*?)</issue>", text, re.DOTALL):
            block = match.group(1)

            def extract(tag: str) -> str:
                # Use greedy match for comment (may contain code with angle brackets)
                if tag == "comment":
                    m = re.search(rf"<{tag}>(.*)</{tag}>", block, re.DOTALL)
                else:
                    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
                return m.group(1).strip() if m else ""

            file_path = extract("file_path")
            line_str = extract("line_number")
            if not file_path:
                continue

            # Parse line number, default to 1
            try:
                line_number = int(line_str.split()[0]) if line_str else 1
            except (ValueError, IndexError):
                line_number = 1

            confidence_str = extract("confidence")
            try:
                confidence = float(confidence_str)
            except ValueError:
                confidence = 0.7

            comment = extract("comment")
            if not comment:
                continue  # Skip issues with no description

            issues.append(ReviewIssue(
                file_path=file_path,
                line_number=line_number,
                category=extract("category") or "logic_error",
                severity=extract("severity") or "medium",
                confidence=confidence,
                comment=comment,
                source_pass="review",
            ))
        return issues

    @staticmethod
    def _dedup_issues(issues: list[ReviewIssue]) -> list[ReviewIssue]:
        """Remove duplicate issues that describe the same bug pattern.

        Two issues are duplicates if they have the same category and their
        comments share significant keyword overlap (>50% of words).
        Keeps the highest-confidence instance.
        """
        # Confidence floor — aligned with category thresholds (0.50-0.70)
        issues = [i for i in issues if i.confidence >= 0.60]
        if len(issues) <= 1:
            return issues

        def _keywords(text: str) -> set[str]:
            """Extract significant words (>3 chars) from text."""
            return {w.lower().strip(".,;:()\"'`") for w in text.split() if len(w) > 3}

        def _similarity(a: str, b: str) -> float:
            """Jaccard similarity of keyword sets."""
            ka, kb = _keywords(a), _keywords(b)
            if not ka or not kb:
                return 0.0
            return len(ka & kb) / len(ka | kb)

        # Sort by confidence descending — keep the best version
        sorted_issues = sorted(issues, key=lambda x: x.confidence, reverse=True)
        kept = []
        for issue in sorted_issues:
            is_dup = False
            for existing in kept:
                sim = _similarity(issue.comment, existing.comment)
                # Thresholds: tighter for same file+category (likely same root cause),
                # standard for same category, higher for cross-category
                if issue.category == existing.category and issue.file_path == existing.file_path:
                    threshold = 0.25  # Same file + category = very likely same bug
                elif issue.file_path == existing.file_path:
                    threshold = 0.30  # Same file, different category = likely related
                elif issue.category == existing.category:
                    threshold = 0.35
                else:
                    threshold = 0.50
                if sim > threshold:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(issue)

        # Per-file cap: max 3 issues per file (already sorted by confidence desc)
        from collections import Counter
        file_counts: Counter = Counter()
        capped = []
        for issue in kept:
            file_counts[issue.file_path] += 1
            if file_counts[issue.file_path] <= 3:
                capped.append(issue)
        return capped

    # ---------- Diff formatting ----------

    def _build_diff_text(self, file_diffs: list[FileDiff], max_chars: int = 300000) -> str:
        """Build combined diff text from file diffs with XML formatting."""
        diff_sections = []
        total_chars = 0

        for fd in file_diffs:
            section = f'\n<file path="{fd.file_path}" language="{fd.language}">\n{fd.raw_diff}\n</file>'
            if total_chars + len(section) > max_chars:
                remaining = max_chars - total_chars - 200
                if remaining > 500:
                    section = f'\n<file path="{fd.file_path}" language="{fd.language}">\n{fd.raw_diff[:remaining]}\n[truncated]\n</file>'
                    diff_sections.append(section)
                break
            diff_sections.append(section)
            total_chars += len(section)

        return "\n".join(diff_sections)

    # ---------- Tools ----------

    def _build_tools(self, repo_path: str | None) -> tuple[list[dict] | None, dict | None]:
        """Build the investigation tool list for agentic code review.

        WarpGrep is listed first as the primary investigation tool.
        """
        if not repo_path:
            return None, None

        tools = []

        # WarpGrep first — primary investigation tool
        warpgrep_tool_def = None
        if self.config.morph_api_key and self.config.warpgrep_tool_enabled:
            warpgrep_tool_def = create_warpgrep_tool(repo_path, self.config.morph_api_key)
            tools.append({
                "name": warpgrep_tool_def["name"],
                "description": warpgrep_tool_def["description"],
                "input_schema": warpgrep_tool_def["input_schema"],
            })

        tools.extend([
            {
                "name": "read_file",
                "description": (
                    "Read a file from the repository. Returns file contents with line numbers. "
                    "Use to read full context around changed code, callers, implementations, tests. "
                    "You can optionally specify line ranges for long files."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "File path relative to repo root",
                        },
                        "lines": {
                            "type": "string",
                            "description": "Optional line ranges, e.g. '1-50,100-120'. Omit to read entire file.",
                        },
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "grep",
                "description": (
                    "Search for a regex pattern across the repository using ripgrep. "
                    "Returns matching lines with file paths and line numbers. "
                    "Supports full regex syntax. Filter files with the glob parameter. "
                    "For semantic code search (understanding how APIs work, finding related patterns), "
                    "use codebase_search instead."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Regex pattern to search for",
                        },
                        "path": {
                            "type": "string",
                            "description": "Directory to search in. Defaults to repo root.",
                        },
                        "glob": {
                            "type": "string",
                            "description": "Glob pattern to filter files, e.g. '*.py', '*.{ts,tsx}'",
                        },
                    },
                    "required": ["pattern"],
                },
            },
            {
                "name": "glob",
                "description": (
                    "Find files by name pattern. Supports glob patterns like '**/*.py' or 'src/**/*.ts'. "
                    "Returns matching file paths. Use when you need to find files by name."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Glob pattern to match files, e.g. '**/*.py', 'src/**/test_*.ts'",
                        },
                        "path": {
                            "type": "string",
                            "description": "Directory to search in. Defaults to repo root.",
                        },
                    },
                    "required": ["pattern"],
                },
            },
            {
                "name": "list_directory",
                "description": (
                    "List directory contents up to 3 levels deep. "
                    "Use to understand project layout or find related files."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path relative to repo root. Use '.' for root.",
                        },
                    },
                    "required": ["path"],
                },
            },
            {
                "name": "bash",
                "description": (
                    "Execute a shell command in the repository directory. "
                    "Use for operations not covered by other tools: running type checkers, "
                    "checking build configs, inspecting package.json, etc."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {
                            "type": "string",
                            "description": "Shell command to execute",
                        },
                    },
                    "required": ["command"],
                },
            },
        ])

        return tools, warpgrep_tool_def

    def _execute_tool(self, tool_call: ProviderToolCall, repo_path: str, warpgrep_tool_def: dict | None) -> dict:
        """Execute a single tool call and return the provider-formatted tool result."""
        import subprocess
        import sys
        from pathlib import Path
        from pr_review_agent.warpgrep.client import (
            _execute_grep, _execute_read, _execute_list_directory,
        )

        name = tool_call.name
        inp = tool_call.input

        if name == "read_file":
            result_text = _execute_read(repo_path, inp.get("path", ""), inp.get("lines"))
            return self.provider.format_tool_result(tool_call, result_text)

        elif name == "grep":
            sub_dir = inp.get("path", ".")
            result_text = _execute_grep(
                repo_path,
                inp.get("pattern", ""),
                sub_dir,
                inp.get("glob"),
            )
            return self.provider.format_tool_result(tool_call, result_text)

        elif name == "glob":
            pattern = inp.get("pattern", "")
            search_dir = Path(repo_path) / inp.get("path", ".")
            try:
                import glob as glob_mod
                matches = sorted(glob_mod.glob(str(search_dir / pattern), recursive=True))
                # Make paths relative to repo root
                matches = [str(Path(m).relative_to(repo_path)) for m in matches if Path(m).is_file()]
                if len(matches) > 200:
                    result_text = "\n".join(matches[:200]) + f"\n... ({len(matches)} total, showing first 200)"
                elif matches:
                    result_text = "\n".join(matches)
                else:
                    result_text = "No files matched."
            except Exception as e:
                result_text = f"Error: {e}"
            return self.provider.format_tool_result(tool_call, result_text)

        elif name == "list_directory":
            result_text = _execute_list_directory(
                repo_path,
                inp.get("path", "."),
                None,
            )
            return self.provider.format_tool_result(tool_call, result_text)

        elif name == "bash":
            command = inp.get("command", "")
            try:
                r = subprocess.run(
                    command, shell=True, capture_output=True, text=True,
                    timeout=30, cwd=repo_path,
                )
                output = r.stdout
                if r.stderr:
                    output += f"\nSTDERR:\n{r.stderr}"
                if r.returncode != 0:
                    output += f"\n(exit code {r.returncode})"
                result_text = output.strip() or "(no output)"
            except subprocess.TimeoutExpired:
                result_text = "Error: command timed out (30s limit)"
            except Exception as e:
                result_text = f"Error: {e}"
            return self.provider.format_tool_result(tool_call, result_text[:50000])

        elif name == WARPGREP_TOOL_NAME and warpgrep_tool_def:
            query = inp.get("search_string", "")
            print(f"    WarpGrep: {query[:80]}", file=sys.stderr)
            result_text = execute_warpgrep_tool(inp, warpgrep_tool_def)
            return self.provider.format_tool_result(
                tool_call, result_text if result_text else "No results found."
            )

        else:
            return self.provider.format_tool_result(
                tool_call, f"Unknown tool: {name}", is_error=True
            )

    # ---------- API call with retry ----------

    def _call_api_with_retry(self, tools, messages, max_retries=5, call_type: str = "review") -> LLMResponse:
        """Call LLM provider with exponential backoff on rate limit errors."""
        import sys

        for attempt in range(max_retries):
            try:
                t_api_start = time.monotonic()
                response = self.provider.chat(
                    messages=messages,
                    system=self.active_system_prompt,
                    tools=tools,
                    max_tokens=self.config.max_tokens,
                    model=self.config.model,
                )
                api_duration = round(time.monotonic() - t_api_start, 2)
                if hasattr(self, '_metrics'):
                    self._metrics.api_calls += 1
                    self._metrics.total_input_tokens += response.usage.input_tokens
                    self._metrics.total_output_tokens += response.usage.output_tokens
                self._emit("review.api_call", {
                    "call_type": call_type,
                    "model": str(self.config.model),
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cache_read_tokens": response.usage.cache_read_tokens,
                    "stop_reason": response.stop_reason,
                    "duration_s": api_duration,
                })
                return response
            except self.provider.rate_limit_exception:
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt * 10  # 10s, 20s, 40s, 80s, 160s
                print(f"  Rate limited, retrying in {wait}s (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                time.sleep(wait)

    # ---------- LLM calls ----------

    def _call_opus(self, prompt: str, thinking_budget: int = 10000) -> str:
        """Call LLM without tools (fallback when no repo_path)."""
        import sys

        for attempt in range(2):
            messages = [{"role": "user", "content": prompt}]
            try:
                response = self.provider.chat(
                    messages=messages,
                    system=self.active_system_prompt,
                    max_tokens=self.config.max_tokens,
                    model=self.config.model,
                )

                result = "\n".join(response.text_parts)
                if result.strip():
                    return result

                if attempt == 0:
                    print(f"  WARNING: Empty response (stop={response.stop_reason}), retrying...", file=sys.stderr)
                    continue
                return ""

            except Exception as e:
                if attempt == 0:
                    print(f"  API error: {e}, retrying...", file=sys.stderr)
                    continue
                print(f"  API error on retry: {e}", file=sys.stderr)
                return ""

        return ""

    # ---------- Agentic loop ----------

    def _agentic_loop(
        self,
        messages: list[dict],
        tools: list[dict],
        repo_path: str,
        warpgrep_tool_def: dict | None,
        thinking_budget: int,
        max_tool_rounds: int = 50,
        changed_files: list[str] | None = None,
    ) -> tuple[str, list[dict]]:
        """Run the LLM with tools in an agentic loop.

        Returns (all_text, trace) where all_text is accumulated text from ALL
        rounds (so <issue> tags emitted mid-investigation are captured) and
        trace is a list of tool call records.
        """
        import sys

        tool_counts = self._metrics.tool_counts
        trace: list[dict] = []
        all_text_parts: list[str] = []  # Accumulate text across ALL rounds
        _coverage_nudge_sent = False

        for round_num in range(max_tool_rounds):
            self._metrics.tool_rounds += 1
            response = self._call_api_with_retry(tools, messages)
            self._metrics.api_calls_review += 1

            _raw_text = "\n".join(response.text_parts)
            all_text_parts.append(_raw_text)
            self._emit("review.agentic_round", {
                "round_num": round_num,
                "tool_calls_this_round": len(response.tool_calls),
                "tools_used": [tc.name for tc in response.tool_calls],
                "stop_reason": response.stop_reason,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cumulative_tool_counts": dict(self._metrics.tool_counts),
                "response_text": _raw_text[:2000],
                "response_text_length": len(_raw_text),
            })

            if not response.tool_calls:
                # If model stopped tool-calling but produced no review text,
                # prompt it to write the review based on its investigation.
                if not _raw_text.strip() and round_num > 0:
                    print(f"  Empty response after {round_num+1} rounds, prompting for review...", file=sys.stderr)
                    messages.append(self.provider.format_assistant_message(response))
                    messages.append({"role": "user", "content": (
                        "You've finished investigating. Now write your code review "
                        "using <issue> XML tags for each bug. If you found no bugs, say so."
                    )})
                    followup = self._call_api_with_retry(tools, messages)
                    self._metrics.api_calls_review += 1
                    followup_text = "\n".join(followup.text_parts)
                    all_text_parts.append(followup_text)
                    self._emit("review.empty_response_followup", {
                        "round_num": round_num,
                        "followup_text": followup_text[:2000],
                        "followup_text_length": len(followup_text),
                    })
                # Targeted follow-up: ask model to check specific high-miss patterns
                if round_num >= 1 and any(all_text_parts):
                    messages.append(self.provider.format_assistant_message(response))
                    messages.append({"role": "user", "content": (
                        "Good investigation. Now do a targeted verification sweep. For each check below, "
                        "do it RIGHT NOW — don't just acknowledge it, actually perform the check:\n\n"
                        "1. READ every new method/variable/class name in the diff character-by-character. "
                        "Report any typo immediately (transposed letters, missing letters, wrong suffix).\n"
                        "2. LIST every new import, callback, or hook registration in the diff. "
                        "For each one, grep for its definition. If it doesn't exist, report it.\n"
                        "3. FIND any groups of similar lines (null checks, filters, conditionals). "
                        "Read each line independently — is the variable/key correct for THAT line?\n"
                        "4. CHECK if any migration or bulk insert stores data without normalization "
                        "that read queries expect (lower(), strip()). Also check shell commands for OS-specific syntax.\n"
                        "5. VERIFY any loop with early exit — does it clean up remaining items?\n"
                        "6. PATTERN REPLICATION: If you found ANY bug above, grep for the same code pattern "
                        "(function call, variable, string literal) in ALL other changed files. The same mistake "
                        "is often copy-pasted across sibling files.\n"
                        "7. COVERAGE: List every changed file from the diff. For any file you haven't deeply "
                        "investigated yet, do a quick read and check for the same patterns you found elsewhere.\n\n"
                        "Only report bugs you can verify. If you already covered these, say 'No additional issues.'"
                    )})
                    # Allow sweep to run a mini agentic loop (up to 4 tool rounds)
                    for _sweep_round in range(4):
                        sweep_response = self._call_api_with_retry(tools, messages)
                        self._metrics.api_calls_review += 1
                        if sweep_response.text_parts:
                            all_text_parts.append("\n".join(sweep_response.text_parts))
                        if not sweep_response.tool_calls:
                            break
                        messages.append(self.provider.format_assistant_message(sweep_response))
                        sweep_tool_results = []
                        for tc in sweep_response.tool_calls:
                            tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
                            result = self._execute_tool(tc, repo_path, warpgrep_tool_def)
                            # Record sweep tool calls in trace
                            result_text = result.get("content", "") if isinstance(result, dict) else str(result)
                            trace.append({
                                "round": f"sweep_{_sweep_round}",
                                "tool": tc.name,
                                "input": tc.input,
                                "output_len": len(result_text) if isinstance(result_text, str) else 0,
                                "is_error": result.get("is_error", False) if isinstance(result, dict) else False,
                            })
                            sweep_tool_results.append(result)
                        messages.append({"role": "user", "content": sweep_tool_results})
                summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
                print(f"  Loop done round={round_num} (tools: {summary or 'none'})", file=sys.stderr)
                return "\n".join(all_text_parts), trace

            # Execute all tool calls
            messages.append(self.provider.format_assistant_message(response))
            tool_results = []
            for tc in response.tool_calls:
                tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1

                t_tool = time.monotonic()
                result = self._execute_tool(tc, repo_path, warpgrep_tool_def)
                tool_duration = round(time.monotonic() - t_tool, 2)

                # Extract text for telemetry (provider-specific result format)
                if isinstance(result, dict):
                    result_text = result.get("content", "") or result.get("response", {}).get("result", "")
                else:
                    result_text = str(result)

                # Record trace
                trace.append({
                    "round": round_num,
                    "tool": tc.name,
                    "input": tc.input,
                    "output_len": len(result_text) if isinstance(result_text, str) else 0,
                    "is_error": result.get("is_error", False),
                })

                tool_input_preview = ""
                if tc.input:
                    tool_input_preview = str(tc.input)[:300]

                is_warpgrep = tc.name == "codebase_search"
                event_data = {
                    "tool_name": tc.name,
                    "tool_input_preview": tool_input_preview,
                    "success": "Error:" not in result_text[:100],
                    "duration_s": tool_duration,
                    "result_size_chars": len(result_text),
                    "result_preview": result_text[:300],
                    "round_num": round_num,
                }
                if is_warpgrep:
                    event_data["warpgrep_query"] = tc.input.get("search_string", "") if isinstance(tc.input, dict) else ""
                    event_data["warpgrep_result_count"] = result_text.count("--- ")
                self._emit("review.tool_call", event_data)

                if is_warpgrep and (not result_text.strip() or "Error:" in result_text[:100]):
                    self._emit("review.warpgrep_failed", {
                        "warpgrep_query": event_data.get("warpgrep_query", ""),
                        "error": result_text[:500],
                        "round_num": round_num,
                        "duration_s": tool_duration,
                        "level": "warning",
                    })

                tool_results.append(result)
            messages.append({"role": "user", "content": tool_results})

            # Coverage nudge: at ~40% through budget, check if files are being skipped
            nudge_round = max(8, int(max_tool_rounds * 0.4))
            if (
                not _coverage_nudge_sent
                and round_num == nudge_round
                and changed_files
                and len(changed_files) > 3
            ):
                _coverage_nudge_sent = True
                # Determine which files have been touched by tool calls
                investigated = set()
                for entry in trace:
                    inp = entry.get("input", {})
                    tool_name = entry.get("tool", "")
                    if tool_name == "read_file":
                        investigated.add(inp.get("path", ""))
                    elif tool_name == "grep":
                        p = inp.get("path", "")
                        if p and p != ".":
                            for cf in changed_files:
                                if cf.startswith(p.rstrip("/") + "/") or cf == p:
                                    investigated.add(cf)
                # Check coverage
                uninvestigated = [
                    f for f in changed_files
                    if f not in investigated
                    and not any(f.startswith(inv.rstrip("/") + "/") for inv in investigated if "/" in inv)
                ]
                if len(uninvestigated) >= 3:
                    file_list = "\n".join(f"  - {f}" for f in uninvestigated[:15])
                    nudge = (
                        f"COVERAGE ALERT: You are {round_num} rounds into your {max_tool_rounds}-round budget. "
                        f"These {len(uninvestigated)} changed files have NOT been investigated yet:\n{file_list}\n"
                        f"Shift your remaining budget to these files. Read each one and check for bugs. "
                        f"Do not revisit files you've already examined."
                    )
                    messages.append({"role": "user", "content": nudge})
                    print(f"  Coverage nudge at round {round_num}: {len(uninvestigated)} uninvestigated files", file=sys.stderr)

        # Hit max rounds -- prompt for final review text
        summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
        print(f"  Max tool rounds reached (tools: {summary})", file=sys.stderr)

        messages.append({"role": "user", "content": (
            "You've finished investigating. Now write your code review "
            "using <issue> XML tags for each bug. If you found no bugs, say so."
        )})
        final_response = self._call_api_with_retry(tools, messages)
        self._metrics.api_calls_review += 1
        if final_response.text_parts:
            all_text_parts.append("\n".join(final_response.text_parts))

        return "\n".join(all_text_parts), trace

    # ---------- Judge (passthrough, kept for evolver compatibility) ----------

    def judge_issues(
        self,
        issues: list[ReviewIssue],
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
    ) -> list[ReviewIssue]:
        """Passthrough. The agentic review prompt handles FP avoidance directly."""
        return issues
