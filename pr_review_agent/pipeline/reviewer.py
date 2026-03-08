"""Opus 4.6 agentic code reviewer.

Single agentic loop: the model investigates the codebase (WarpGrep 4-6+ times),
reviews the diff, and writes findings — all in one continuous flow.
A separate structured output call extracts issues as XML.
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
    category: str = Field(description="Bug category: logic_error|incorrect_value|api_misuse|race_condition|null_reference|type_error|security|localization|test_correctness|portability")
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

        The model investigates the codebase (WarpGrep 4-6+ times first),
        then reviews the diff and writes findings — all in one continuous
        agentic loop. A separate call extracts issues as XML.
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

**Step 1: Investigate the codebase.** Before forming any opinions, use `warpgrep_codebase_search` to understand the codebase context. Make at least 4-6 searches targeting your uncertainties about the changed code.

How to search effectively with WarpGrep — it is a search AGENT, not a grep tool. Ask it QUESTIONS about behavior and relationships, not keyword lookups:
- GOOD: "What concurrency model protects [shared state]? Are there locks or transactions?"
- GOOD: "Who calls [changed function] and how do callers handle the return value?"
- GOOD: "How does [framework API] behave when given an empty object or nil argument?"
- GOOD: "What are all implementations of [interface] and do they all handle [edge case]?"
- BAD: "[ClassName] constructor and [field] property" — this is a keyword lookup, use `grep` instead
- BAD: "[functionName] function and its callers in [file]" — use `grep` for exact symbol lookups
- Search for each major area of the diff separately. If the PR touches 3 subsystems, do at least one search per subsystem.

**Step 2: Investigate every changed file.** Don't stop after finding one bug. Budget your investigation across ALL changed files. For every non-trivial change:
- Search for callers. Will they handle the new behavior correctly?
- If a function signature, interface, or return type changed, grep for ALL callers and implementers. Don't assume they were all updated.
- If a constant or key name changed, find where the old value was referenced. Was everything updated?
- If concurrency is involved (locks, goroutines, threads, async), find ALL readers and writers of the shared state. Ask: "What happens if two requests do this simultaneously?" Look for non-atomic read-modify-write patterns (e.g. `retryCount + 1` without a lock).
- If code branches on environment variables or feature flags, analyze BOTH paths. Bugs hide in less-tested paths.
- If a framework API is used, verify its behavior with the specific arguments used. Edge cases like empty objects, nil values, or platform differences are where bugs hide.

**Step 3: Write your review.** For each confirmed bug, name the exact code, explain what's wrong, and describe the runtime consequence. Every issue must be backed by evidence from your tool calls.

Important investigation rules:
- Don't stop at the first finding. Keep investigating ALL changed files.
- Report each unique bug ONCE. If forEach+async appears in 3 files, report it once and mention the other files. Two candidates for the same root cause = one report.
- Before claiming "X doesn't exist" or "Y is not imported", search the entire repo. Definitions exist outside the diff.
- Don't over-extrapolate edge cases. Only report bugs you can demonstrate via actual code paths, not theoretical "what if" scenarios.
- Check for naming bugs: method name typos, property name typos, error messages that contradict the operation.

BUDGET YOUR INVESTIGATION: You have a limited number of tool rounds. Do NOT spend more than 2-3 searches on the same question. If a symbol, class, or file doesn't appear after 2 searches, it either doesn't exist or isn't relevant — move on. Spread your investigation across ALL areas of the diff rather than deep-diving into one area. A common failure mode is spending 15+ rounds chasing one question while ignoring the rest of the PR.

FREQUENTLY MISSED PATTERNS (check these explicitly for every PR):
- Method/variable name typos (missing letters, wrong suffixes)
- Inconsistent string constants: metric tags, error codes, or keys that use slightly different names in different places (e.g., "shard" vs "shards")
- Test setup that contradicts the test's intent (e.g., cache values set to "deny" but test claims "allow")
- API/response format changes that break existing consumers or callers
- Reduced mutex/lock scope compared to original code (creates race windows)
- ORM updates with empty data objects (skip auto-updated fields like @updatedAt)
- Dictionary/map ordering assumptions (e.g., zip(keys, dict.values()) loses alignment)
- Shell commands with platform-specific syntax (e.g., macOS sed -i vs Linux)

IMPORTANT: Report every bug you find that you believe is real. It is better to report a borderline bug than to miss a real one. Even if you only have moderate confidence (0.5-0.7), report it — the confidence score communicates your uncertainty. Do NOT hold back findings.

After your investigation, go through EVERY changed file in the diff and ask: "Did I check this file for bugs?" If you skipped any files, review them now."""

        if tools:
            messages = [{"role": "user", "content": prompt}]
            try:
                review_text, trace = self._agentic_loop(
                    messages, tools, repo_path, warpgrep_tool_def,
                    thinking_budget=10000, max_tool_rounds=30,
                )
            except Exception as e:
                print(f"  Review failed: {e}", file=sys.stderr)
                return []
        else:
            review_text = self._call_opus(prompt)
            trace = []

        # Store trace for debugging
        self._last_trace = trace

        # Extract structured issues from the freeform review
        issues = self._extract_issues(review_text, combined_diff)

        # Post-processing dedup: merge issues with same category and overlapping descriptions
        issues = self._dedup_issues(issues)

        print(f"  Review complete: {len(issues)} issues", file=sys.stderr)
        self.last_metrics = self._metrics
        return issues

    def _extract_issues(self, review_text: str, diff_text: str) -> list[ReviewIssue]:
        """Extract structured issues from freeform review text using XML output."""
        extraction_prompt = f"""Extract the bugs from this code review into XML format.

<review>
{review_text[:20000]}
</review>

For each bug the reviewer identified, output an <issue> element. Only extract issues the reviewer explicitly identified as bugs. Do not invent new ones. If the review found no bugs, output <issues></issues>.

Output format:
<issues>
<issue>
<file_path>path/to/file.py</file_path>
<line_number>42</line_number>
<category>logic_error</category>
<severity>high</severity>
<confidence>0.85</confidence>
<comment>Description of the bug: what code is wrong, what it should be, and the runtime consequence</comment>
</issue>
</issues>

Valid categories: logic_error, incorrect_value, api_misuse, race_condition, null_reference, type_error, security, localization, test_correctness, portability
Valid severities: critical, high, medium, low
Confidence: 0.0-1.0 based on how certain the reviewer was"""

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
                "issues_extracted": len(issues),
                "duration_s": extract_duration,
                "success": True,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            })
            return issues
        except Exception as exc:
            print(f"  Extraction failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            self._emit("review.extraction_error", {
                "review_text_length": len(review_text),
                "error": str(exc),
                "error_type": type(exc).__name__,
            })
            return []

    @staticmethod
    def _parse_xml_issues(text: str) -> list[ReviewIssue]:
        """Parse XML-formatted issues from extraction response."""
        import re
        issues = []
        for match in re.finditer(r"<issue>(.*?)</issue>", text, re.DOTALL):
            block = match.group(1)

            def extract(tag: str) -> str:
                m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
                return m.group(1).strip() if m else ""

            file_path = extract("file_path")
            line_str = extract("line_number")
            if not file_path or not line_str:
                continue
            try:
                line_number = int(line_str)
            except ValueError:
                continue

            confidence_str = extract("confidence")
            try:
                confidence = float(confidence_str)
            except ValueError:
                confidence = 0.5

            issues.append(ReviewIssue(
                file_path=file_path,
                line_number=line_number,
                category=extract("category") or "logic_error",
                severity=extract("severity") or "medium",
                confidence=confidence,
                comment=extract("comment"),
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
                if issue.category == existing.category:
                    sim = _similarity(issue.comment, existing.comment)
                    if sim > 0.35:
                        is_dup = True
                        break
            if not is_dup:
                kept.append(issue)

        return kept

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
                    "use warpgrep_codebase_search instead."
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
    ) -> tuple[str, list[dict]]:
        """Run the LLM with tools in an agentic loop.

        Returns (review_text, trace) where trace is a list of tool call records.
        """
        import sys

        tool_counts = self._metrics.tool_counts
        trace: list[dict] = []

        for round_num in range(max_tool_rounds):
            self._metrics.tool_rounds += 1
            response = self._call_api_with_retry(tools, messages)
            self._metrics.api_calls_review += 1

            self._emit("review.agentic_round", {
                "round_num": round_num,
                "tool_calls_this_round": len(response.tool_calls),
                "tools_used": [tc.name for tc in response.tool_calls],
                "stop_reason": response.stop_reason,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "cumulative_tool_counts": dict(self._metrics.tool_counts),
            })

            if not response.tool_calls:
                result = "\n".join(response.text_parts)
                # If model stopped tool-calling but produced no review text,
                # prompt it to write the review based on its investigation.
                if not result.strip() and round_num > 0:
                    print(f"  Empty response after {round_num+1} rounds, prompting for review...", file=sys.stderr)
                    messages.append(self.provider.format_assistant_message(response))
                    messages.append({"role": "user", "content": (
                        "You've finished investigating. Now write your code review. "
                        "For each bug you found, name the exact code, explain what's wrong, "
                        "and describe the runtime consequence. If you found no bugs, say so."
                    )})
                    followup = self._call_api_with_retry(tools, messages)
                    self._metrics.api_calls_review += 1
                    result = "\n".join(followup.text_parts)
                summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
                print(f"  Loop done round={round_num} (tools: {summary or 'none'})", file=sys.stderr)
                return result, trace

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

                is_warpgrep = tc.name == "warpgrep_codebase_search"
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

        # Hit max rounds -- prompt for final review text
        summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
        print(f"  Max tool rounds reached (tools: {summary})", file=sys.stderr)

        messages.append({"role": "user", "content": (
            "You've finished investigating. Now write your code review. "
            "For each bug you found, name the exact code, explain what's wrong, "
            "and describe the runtime consequence. If you found no bugs, say so."
        )})
        final_response = self._call_api_with_retry(tools, messages)
        self._metrics.api_calls_review += 1

        return ("\n".join(final_response.text_parts) if final_response.text_parts else ""), trace

    # ---------- Judge (passthrough, kept for evolver compatibility) ----------

    def judge_issues(
        self,
        issues: list[ReviewIssue],
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
    ) -> list[ReviewIssue]:
        """Passthrough. The agentic review prompt handles FP avoidance directly."""
        return issues
