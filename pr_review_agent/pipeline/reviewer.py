"""Opus 4.6 end-to-end agentic code reviewer.

Single agentic loop: the model reads the diff, investigates the codebase
with tools, and reports bugs via the report_issue tool. No intermediate
structured plans, no separate extraction step, no checklists.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field

import anthropic

from pr_review_agent.config import Config
from pr_review_agent.pipeline.diff_parser import FileDiff
from pr_review_agent.prompts.review import get_language_hint
from pr_review_agent.prompts.system import SYSTEM_PROMPT
from pr_review_agent.warpgrep.client import (
    create_warpgrep_tool,
    execute_warpgrep_tool,
    WARPGREP_TOOL_NAME,
)


# ---------- report_issue tool definition ----------

REPORT_ISSUE_TOOL = {
    "name": "report_issue",
    "description": (
        "Report a bug you found. Call once per bug as you find it during "
        "investigation. Some bugs are obvious from the diff (typos, copy-paste, "
        "wrong locale); others need tool-based investigation to confirm. "
        "Report both kinds. Do not report the same bug twice."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "File path from the diff header",
            },
            "line_number": {
                "type": "integer",
                "description": "Line number in the new code",
            },
            "category": {
                "type": "string",
                "enum": [
                    "logic_error", "incorrect_value", "api_misuse",
                    "race_condition", "null_reference", "type_error",
                    "security", "localization", "test_correctness", "portability",
                ],
                "description": "Bug category",
            },
            "severity": {
                "type": "string",
                "enum": ["critical", "high", "medium", "low"],
                "description": "Issue severity",
            },
            "confidence": {
                "type": "number",
                "description": "0.0 to 1.0 — how certain this is a real bug",
            },
            "comment": {
                "type": "string",
                "description": "What code is wrong, what it should be, and the runtime consequence",
            },
        },
        "required": ["file_path", "line_number", "category", "severity", "confidence", "comment"],
    },
}


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
        self._num_passes: int | None = None

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

    # ---------- Main entry point ----------

    def review_pr(
        self,
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
        num_passes: int | None = None,
    ) -> list[ReviewIssue]:
        """End-to-end agentic review.

        One agentic loop where the model:
        1. Reads and understands the diff
        2. Investigates the codebase with tools (warpgrep, grep, read_file)
        3. Reports confirmed bugs via the report_issue tool
        """
        import sys

        combined_diff = self._build_diff_text(file_diffs)

        # Gather language hints
        languages = {fd.language for fd in file_diffs}
        lang_hints = "\n".join(
            f"- **{lang}**: {get_language_hint(lang)}"
            for lang in languages
            if get_language_hint(lang)
        )

        prompt = f"""You are a senior engineer reviewing this pull request. Your job is to find real bugs.

## PR Diff
{combined_diff}

{f"## Language Notes{chr(10)}{lang_hints}" if lang_hints else ""}

## How to Review

**Phase 1: Understand the change.** Read the diff. What does it modify? What assumptions does the old code make that the new code changes?

**Phase 2: Investigate impact.** For every non-trivial change, use tools to answer:
- Who calls this function/method? Search for callers. Will they handle the new behavior?
- If a constant or key name changed, find where the old value was referenced. Was everything updated?
- If a loop has break/return/continue, what cleanup or remaining iterations get skipped?
- If concurrency is involved (locks, goroutines, threads, async), search for ALL readers and writers of the shared state. Check that synchronization still covers them.
- If the code uses a framework API (Prisma, Rails, Django, multiprocessing, etc.), search for how that API behaves with the specific arguments used. Edge cases like empty objects, nil values, or platform differences are where bugs hide.

Don't follow a checklist. Let the diff guide your investigation. If something looks suspicious, dig into it. If a change is obviously safe, move on. Spend your time where the risk is.

**Phase 3: Report what you find.** Some bugs are obvious from the diff itself (typos, copy-paste errors, wrong locale text, inverted logic). Report those immediately. For bugs that depend on how other code uses the changed code, confirm with tools first.

The bugs you're looking for are things like:
- Wrong method called (copy-paste error, similar names)
- Logic inverted or unreachable
- Return type/value contract broken for callers
- Cross-file inconsistency (string/key/name doesn't match between files)
- Test that doesn't actually test what it claims (wrong mock, wrong assertion, vacuous check)
- API misuse (forEach+async, wrong arg types, missing await)
- Silent data loss or corruption
- Security regression (auth bypass, SSRF, weakened protection)

What you should NOT report:
- "X might be undefined" when X clearly exists outside the diff
- "Should add null check" without a concrete null path
- "Consider using Y instead of X" when X works fine
- Performance suggestions
- Style preferences
- The same bug reported multiple times. If the same issue (e.g. forEach+async) appears in multiple files, report it ONCE for the most important file.
- Suspected bugs you could easily verify but didn't. If you think a function doesn't exist, grep for it. If you think a type is wrong, read the definition.

When you find a bug, call the `report_issue` tool with the details. Report each bug as you find it during investigation. When done investigating all changes, stop — no need to write a summary.

If you find no bugs, that's fine. Quality over quantity."""

        # Build tools: report_issue is always available, investigation tools when repo_path exists
        tools = [REPORT_ISSUE_TOOL]
        warpgrep_tool_def = None
        if repo_path:
            investigation_tools, warpgrep_tool_def = self._build_tools(repo_path)
            if investigation_tools:
                tools = investigation_tools + [REPORT_ISSUE_TOOL]

        messages = [{"role": "user", "content": prompt}]
        try:
            issues = self._agentic_loop(
                messages, tools, repo_path, warpgrep_tool_def,
                thinking_budget=10000, max_tool_rounds=50,
            )
        except Exception as e:
            print(f"  Review failed: {e}", file=sys.stderr)
            return []

        print(f"  Review complete: {len(issues)} issues", file=sys.stderr)
        return issues

    # ---------- Diff formatting ----------

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
                    "Use this to understand the full context around changed code — what happens "
                    "before and after the diff. Read callers, read implementations, read tests. "
                    "Fast local operation — use it freely."
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
                "name": "grep_pattern",
                "description": (
                    "Search for an exact regex pattern across the repository using ripgrep. "
                    "Returns matching lines with file paths and line numbers. "
                    "Use for exact-match lookups: specific function names, variable references, "
                    "import statements, string literals. For understanding code semantics, "
                    "finding related patterns, or investigating how APIs work, use "
                    "warpgrep_codebase_search instead."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "pattern": {
                            "type": "string",
                            "description": "Regex pattern to search for",
                        },
                        "sub_dir": {
                            "type": "string",
                            "description": "Subdirectory to search in. Defaults to entire repo.",
                        },
                        "glob": {
                            "type": "string",
                            "description": "File glob filter, e.g. '*.py', '*.ts'. Defaults to all files.",
                        },
                    },
                    "required": ["pattern"],
                },
            },
            {
                "name": "list_directory",
                "description": (
                    "List directory structure up to 3 levels deep. "
                    "Use to understand project layout or find related files."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Directory path relative to repo root. Use '.' for root.",
                        },
                        "pattern": {
                            "type": "string",
                            "description": "Optional regex to filter results.",
                        },
                    },
                    "required": ["path"],
                },
            },
        ])

        return tools, warpgrep_tool_def

    def _execute_tool(self, tool_block, repo_path: str | None, warpgrep_tool_def: dict | None) -> dict:
        """Execute a single tool call and return the tool_result."""
        import sys
        from pr_review_agent.warpgrep.client import (
            _execute_grep, _execute_read, _execute_list_directory,
        )

        name = tool_block.name
        inp = tool_block.input

        if name == "report_issue":
            # Handled by _agentic_loop directly; return confirmation
            return {
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": f"Issue recorded: {inp.get('category', '?')} in {inp.get('file_path', '?')}:{inp.get('line_number', '?')}",
            }

        if not repo_path:
            return {
                "type": "tool_result",
                "tool_use_id": tool_block.id,
                "content": "No repository path available for investigation tools.",
                "is_error": True,
            }

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

    # ---------- Agentic loop ----------

    def _agentic_loop(
        self,
        messages: list[dict],
        tools: list[dict],
        repo_path: str | None,
        warpgrep_tool_def: dict | None,
        thinking_budget: int,
        max_tool_rounds: int = 50,
    ) -> list[ReviewIssue]:
        """Run Claude with tools in an agentic loop.

        The model investigates with tools and calls report_issue for each bug.
        Returns the collected list of ReviewIssue objects.
        """
        import sys

        tool_counts: dict[str, int] = {}
        collected_issues: list[ReviewIssue] = []

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

            tool_use_blocks = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_use_blocks.append(block)

            if not tool_use_blocks:
                summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
                print(f"  Review complete (tools: {summary or 'none'})", file=sys.stderr)
                return collected_issues

            # Execute all tool calls, collecting report_issue results
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tool_block in tool_use_blocks:
                tool_counts[tool_block.name] = tool_counts.get(tool_block.name, 0) + 1

                # Collect issues from report_issue calls
                if tool_block.name == "report_issue":
                    inp = tool_block.input
                    collected_issues.append(ReviewIssue(
                        file_path=inp.get("file_path", ""),
                        line_number=inp.get("line_number", 0),
                        category=inp.get("category", "logic_error"),
                        severity=inp.get("severity", "medium"),
                        confidence=inp.get("confidence", 0.5),
                        comment=inp.get("comment", ""),
                        source_pass="review",
                    ))

                result = self._execute_tool(tool_block, repo_path, warpgrep_tool_def)
                tool_results.append(result)
            messages.append({"role": "user", "content": tool_results})

        # Hit max rounds — return what we have
        summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
        print(f"  Max tool rounds reached (tools: {summary})", file=sys.stderr)
        return collected_issues

    # ---------- Judge (passthrough, kept for evolver compatibility) ----------

    def judge_issues(
        self,
        issues: list[ReviewIssue],
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
    ) -> list[ReviewIssue]:
        """Passthrough. The agentic review prompt handles FP avoidance directly."""
        return issues
