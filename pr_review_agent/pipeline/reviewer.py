"""Opus 4.6 end-to-end agentic code reviewer.

Single agentic loop: the model reads the diff, investigates the codebase
with tools, and produces a review. No intermediate structured plans,
no separate query planning phases, no checklists.
"""

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


def _strict_schema(schema: dict) -> dict:
    """Add additionalProperties: false to all object types in a JSON schema.

    Required by the Anthropic structured output API.
    """
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


# ---------- Pydantic models for structured output (final extraction only) ----------

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
        """End-to-end agentic review.

        One agentic loop where the model:
        1. Reads and understands the diff
        2. Investigates the codebase with tools (warpgrep, grep, read_file)
        3. Forms its own understanding of what could be wrong
        4. Produces a final review

        Then one structured output call to extract issues as JSON.
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

Read the diff carefully. Understand what every change does and WHY. Then use the tools to investigate.

You have tools to search and read the codebase. Use them to answer your own questions:
- If a function's behavior changed, find its callers. Will they handle the new behavior correctly?
- If a constant or key name changed, find where the old value was referenced. Was everything updated?
- If error handling changed, trace the error path. Do callers still handle errors correctly?
- If a data structure is being mutated, understand the mutation semantics. Are existing values preserved or clobbered?

Don't follow a checklist. Let the diff guide your investigation. If something looks suspicious, dig into it. If a change is obviously safe, move on. Spend your time where the risk is.

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

After investigating, write your review. For each bug, name the exact code, explain what's wrong, and describe the runtime consequence. If you find no bugs, that's fine — say so.

Quality over quantity. 2 real bugs with evidence > 6 speculative ones."""

        tools, warpgrep_tool_def = self._build_tools(repo_path)

        if tools:
            # Agentic loop: model investigates with tools, then produces review
            messages = [{"role": "user", "content": prompt}]
            try:
                review_text = self._agentic_loop(
                    messages, tools, repo_path, warpgrep_tool_def,
                    thinking_budget=10000, max_tool_rounds=25,
                )
            except Exception as e:
                print(f"  Review failed: {e}", file=sys.stderr)
                return []
        else:
            # No tools available, direct call
            review_text = self._call_opus(prompt)

        # Extract structured issues from the freeform review
        issues = self._extract_issues(review_text, combined_diff)
        print(f"  Review complete: {len(issues)} issues", file=sys.stderr)
        return issues

    def _extract_issues(self, review_text: str, diff_text: str) -> list[ReviewIssue]:
        """Extract structured issues from freeform review text via structured output.

        This is the ONLY place we force structure — after the model has done all
        its thinking and investigation in natural language.
        """
        extraction_prompt = f"""Extract the bugs from this code review into structured JSON.

## The Review
{review_text[:20000]}

For each bug the reviewer identified, extract:
- file_path: the file where the bug is
- line_number: the line number (best guess if not exact)
- category: one of logic_error, incorrect_value, api_misuse, race_condition, null_reference, type_error, security, localization, test_correctness, portability
- severity: critical, high, medium, or low
- confidence: 0.0-1.0 based on how certain the reviewer was
- comment: the full bug description (what's wrong, what it should be, what breaks)

If the review found no bugs, return an empty issues list.
Only extract issues the reviewer explicitly identified as bugs. Do not invent new ones."""

        schema = _strict_schema(ReviewResult.model_json_schema())
        try:
            result_text = self.client.messages.create(
                model=self.config.model,
                max_tokens=8000,
                thinking={"type": "adaptive"},
                temperature=1,
                messages=[{"role": "user", "content": extraction_prompt}],
                output_config={
                    "format": {"type": "json_schema", "schema": schema}
                },
            )
            text_block = next(b for b in result_text.content if b.type == "text")
            return self._parse_structured_issues(text_block.text, source_pass="review")
        except Exception:
            return []

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
        """Build the tool list for agentic code review."""
        if not repo_path:
            return None, None

        tools = [
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
                    "Search for a regex pattern across the repository using ripgrep. "
                    "Returns matching lines with file paths and line numbers. "
                    "Use this to find callers, check cross-file consistency, "
                    "find related code. Fast local operation — use it freely."
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

    # ---------- LLM calls ----------

    def _call_opus(self, prompt: str, thinking_budget: int = 10000, repo_path: str | None = None, timeout: int = 300, output_schema: dict | None = None) -> str:
        """Call Claude with extended thinking, optionally with tools or structured output."""
        import sys

        tools, warpgrep_tool_def = self._build_tools(repo_path)

        for attempt in range(2):
            messages = [{"role": "user", "content": prompt}]

            try:
                if tools:
                    return self._agentic_loop(messages, tools, repo_path, warpgrep_tool_def, thinking_budget, output_schema=output_schema)

                api_kwargs = dict(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    thinking={"type": "adaptive"},
                    temperature=1,
                    system=self.active_system_prompt,
                    messages=messages,
                )
                if output_schema:
                    api_kwargs["output_config"] = {
                        "format": {"type": "json_schema", "schema": output_schema}
                    }

                if output_schema:
                    response = self.client.messages.create(**api_kwargs)
                else:
                    with self.client.messages.stream(**api_kwargs) as stream:
                        response = stream.get_final_message()

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
        output_schema: dict | None = None,
    ) -> str:
        """Run Claude with tools in an agentic loop.

        The model calls tools as many times as it needs. When it stops
        calling tools, we return its text response.
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

            text_parts = []
            tool_use_blocks = []
            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_use_blocks.append(block)

            if not tool_use_blocks:
                result = "\n".join(text_parts)
                summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
                print(f"  Review complete (tools: {summary or 'none'})", file=sys.stderr)
                return result

            # Execute all tool calls
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for tool_block in tool_use_blocks:
                tool_counts[tool_block.name] = tool_counts.get(tool_block.name, 0) + 1
                result = self._execute_tool(tool_block, repo_path, warpgrep_tool_def)
                tool_results.append(result)
            messages.append({"role": "user", "content": tool_results})

        # Hit max rounds — get final text
        summary = ", ".join(f"{n}={c}" for n, c in tool_counts.items())
        print(f"  Max tool rounds reached (tools: {summary})", file=sys.stderr)

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

        return "\n".join(text_parts) if text_parts else ""

    # ---------- Judge (passthrough, kept for evolver compatibility) ----------

    def judge_issues(
        self,
        issues: list[ReviewIssue],
        file_diffs: list[FileDiff],
        repo_path: str | None = None,
    ) -> list[ReviewIssue]:
        """Passthrough. The agentic review prompt handles FP avoidance directly."""
        return issues
