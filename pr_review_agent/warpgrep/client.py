"""WarpGrep SDK client — official Python implementation.

Uses the Morph API's morph-warp-grep-v1 model as a code search subagent.
The model runs in its own context window with up to 4 turns and 8 parallel
tool calls per turn (grep, read, list_directory, finish).

Can be used:
  1. Directly via search_codebase() for standalone queries
  2. As an Anthropic tool via create_warpgrep_tool() for agent integration
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests

# Defaults
API_URL = "https://api.morphllm.com/v1/chat/completions"
MODEL = "morph-warp-grep-v1"
MAX_TURNS = 4
MAX_GREP_LINES = 500
MAX_LIST_LINES = 500
MAX_READ_LINES = 2000

# Retry config for transient API errors (429, SSL, timeouts)
MAX_RETRIES = 3
RETRY_BASE_DELAY = 3.0  # seconds (3s, 6s, 12s backoff)

# Global rate limiter: max 1 concurrent WarpGrep API call
import threading
_api_semaphore = threading.Semaphore(4)  # max 4 concurrent WarpGrep calls

SYSTEM_PROMPT = r"""You are a code search agent. Your task is to find all relevant code for a given search_string.

### workflow
You have exactly 4 turns. The 4th turn MUST be a `finish` call. Each turn allows up to 8 parallel tool calls.

- Turn 1: Map the territory OR dive deep (based on search_string specificity)
- Turn 2-3: Refine based on findings
- Turn 4: MUST call `finish` with all relevant code locations
- You MAY call `finish` early if confident—but never before at least 1 search turn.
- The user strongly prefers if you can call the finish tool early using fewer turns, but quality over speed

Remember, if the task feels easy to you, it is strongly desirable to call 'finish' early using fewer turns, but quality over speed

### tools
Tool calls use nested XML elements

### `list_directory`
Directory tree view. Shows structure of a path, optionally filtered by regex pattern.

Elements:
- `<path>` (required): Directory path to list (use `.` for repo root)
- `<pattern>` (optional): Regex to filter results

### `read`
Read file contents. Supports multiple line ranges.
- Returns numbered lines for easy reference
- ALWAYS include import statements (usually lines 1-20).

Elements:
- `<path>` (required): File path to read
- `<lines>` (optional): Line ranges like "1-50,75-80,100-120"

### `grep`
Search for pattern matches across files with context.

Elements:
- `<pattern>` (required): Search pattern (regex)
- `<sub_dir>` (optional): Subdirectory to search (defaults to `.`)
- `<glob>` (optional): File pattern filter like `*.py`

### `finish`
Submit final answer with all relevant code locations using nested `<file>` elements.

File elements:
- `<path>` (required): File path
- `<lines>` (optional): Line ranges like "1-50,75-80" (`*` for entire file)

<strategy>
**Before your first tool call, classify the search_string:**

| Type | Round 1 Strategy | Early Finish? |
|------|------------------|---------------|
| **Specific** (function name, error string) | 8 parallel greps on likely paths | Often by round 2 |
| **Conceptual** (how does X work) | list_directory + 2-3 broad greps | Rarely early |
| **Exploratory** (find all tests) | list_directory at multiple depths | Usually needs 3 rounds |

**Parallel call patterns:**
- **Shotgun grep**: Same pattern, 8 different directories
- **Variant grep**: 8 pattern variations
- **Funnel**: 1 list_directory + 7 greps
- **Deep read**: 8 reads on identified files

**Tool call quality:**
- High quality calls balance specificity with coverage
- Use root structure knowledge to avoid trivial repo-wide queries
- Use grep results to inform read calls with precise line ranges
</strategy>

<output_format>
EVERY response MUST follow this exact format:

1. Wrap reasoning in `<think>...</think>` tags:
   - Search_string classification
   - Confidence estimate
   - This round's parallel strategy
   - Early finish signals

2. Output up to 8 tool calls using nested XML elements

No commentary outside `<think>`. No explanations after tool calls.
</output_format>

<finishing_requirements>
When calling `finish`:
- Include import section (typically lines 1-20)
- Include all function/class definitions
- Include type definitions, interfaces, or constants
- Better to over-include than leave missing context
</finishing_requirements>
"""


@dataclass
class ToolCall:
    name: str
    args: dict


def _call_api(messages: list, api_key: str, api_url: str = API_URL) -> str:
    """Call WarpGrep model via Morph API with retry logic for transient errors."""
    _api_semaphore.acquire()
    try:
        return _call_api_inner(messages, api_key, api_url)
    finally:
        _api_semaphore.release()


def _call_api_inner(messages: list, api_key: str, api_url: str = API_URL) -> str:
    """Inner API call with retry logic (called under semaphore)."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MODEL,
                    "messages": messages,
                    "temperature": 0.0,
                    "max_tokens": 2048,
                },
                timeout=45,
            )
            # Retry on 429 (rate limit) and 5xx (server errors)
            if resp.status_code == 429 or resp.status_code >= 500:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                print(f"  WarpGrep API {resp.status_code}, retrying in {delay:.0f}s (attempt {attempt + 1}/{MAX_RETRIES})", file=sys.stderr)
                time.sleep(delay)
                last_exc = requests.HTTPError(f"{resp.status_code}", response=resp)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            print(f"  WarpGrep connection error, retrying in {delay:.0f}s (attempt {attempt + 1}/{MAX_RETRIES}): {type(e).__name__}", file=sys.stderr)
            time.sleep(delay)
            last_exc = e
            continue

    # All retries exhausted
    if last_exc:
        raise last_exc
    raise RuntimeError("WarpGrep API call failed after all retries")


def _parse_xml_elements(content: str) -> dict:
    """Parse nested XML elements into dictionary."""
    args = {}
    for match in re.finditer(r"<(\w+)>(.*?)</\1>", content, re.DOTALL):
        key, value = match.group(1), match.group(2).strip()
        if key == "file":
            args.setdefault("files", []).append(_parse_xml_elements(value))
        else:
            args[key] = value
    return args


def _parse_tool_calls(response: str) -> list[ToolCall]:
    """Parse XML tool calls from model response."""
    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
    calls = []
    for name in ["grep", "read", "list_directory", "finish"]:
        for match in re.finditer(rf"<{name}>(.*?)</{name}>", response, re.DOTALL):
            calls.append(ToolCall(name=name, args=_parse_xml_elements(match.group(1))))
    return calls


def _execute_grep(repo: str, pattern: str, sub_dir: str = ".", glob: str | None = None) -> str:
    """Execute ripgrep search."""
    # Strip repo dir name prefix if model included it
    repo_name = Path(repo).name
    if sub_dir.startswith(repo_name + "/"):
        sub_dir = sub_dir[len(repo_name) + 1:]
    elif sub_dir == repo_name:
        sub_dir = "."

    cmd = [
        "rg", "--line-number", "--no-heading", "--color", "never", "-C", "1",
    ]
    if glob:
        cmd.extend(["--glob", glob])
    cmd.extend([pattern, str(Path(repo) / sub_dir)])

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=repo)
        lines = r.stdout.strip().split("\n") if r.stdout.strip() else []
        if len(lines) > MAX_GREP_LINES:
            return "query not specific enough, tool called tried to return too much context and failed"
        return r.stdout.strip() or "no matches"
    except subprocess.TimeoutExpired:
        return "Error: search timed out"
    except Exception as e:
        return f"Error: {e}"


def _execute_read(repo: str, path: str, lines: str | None = None) -> str:
    """Read file contents with optional line ranges."""
    fp = Path(repo) / path
    if not fp.exists():
        # Try stripping the repo directory name prefix (tree includes it)
        repo_name = Path(repo).name
        if path.startswith(repo_name + "/"):
            fp = Path(repo) / path[len(repo_name) + 1:]
        if not fp.exists():
            return f"Error: file not found: {path}"

    try:
        all_lines = fp.read_text(errors="replace").splitlines()
    except Exception as e:
        return f"Error: {e}"

    if lines and lines != "*":
        selected = []
        for part in lines.split(","):
            part = part.strip()
            if "-" in part:
                s, e = map(int, part.split("-"))
            else:
                s = e = int(part)
            selected.extend(range(max(0, s - 1), min(e, len(all_lines))))

        out, prev = [], -2
        for i in sorted(set(selected)):
            if 0 <= i < len(all_lines):
                if prev >= 0 and i > prev + 1:
                    out.append("...")
                out.append(f"{i + 1}|{all_lines[i]}")
                prev = i

        if len(out) > MAX_READ_LINES:
            out = out[:MAX_READ_LINES]
            out.append(f"... truncated ({len(all_lines)} total lines)")
        return "\n".join(out)

    out = [f"{i + 1}|{l}" for i, l in enumerate(all_lines[:MAX_READ_LINES])]
    if len(all_lines) > MAX_READ_LINES:
        out.append(f"... truncated ({len(all_lines)} total lines)")
    return "\n".join(out)


def _execute_list_directory(repo: str, path: str, pattern: str | None = None) -> str:
    """List directory structure."""
    # Strip repo dir name prefix if model included it
    repo_name = Path(repo).name
    if path.startswith(repo_name + "/"):
        path = path[len(repo_name) + 1:]
    elif path == repo_name:
        path = "."

    dp = Path(repo) / path
    if not dp.exists():
        return f"Error: directory not found: {path}"

    # Try tree first, fallback to manual walk
    try:
        cmd = [
            "tree", "-L", "3", "-i", "-F", "--noreport",
            "-I", "__pycache__|node_modules|.git|*.pyc|.DS_Store|.venv|venv|dist|build",
            str(dp),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5, cwd=repo)
        lines = r.stdout.strip().split("\n") if r.stdout.strip() else []
    except FileNotFoundError:
        lines = _fallback_list_dir(dp)

    if pattern:
        try:
            compiled = re.compile(pattern)
            lines = [l for l in lines if compiled.search(l)]
        except re.error:
            pass

    if len(lines) > MAX_LIST_LINES:
        return "query not specific enough, tool called tried to return too much context and failed"
    return "\n".join(lines)


def _fallback_list_dir(dir_path: Path, max_depth: int = 3) -> list[str]:
    """Fallback directory listing without tree command."""
    lines = []
    skip = {"node_modules", "__pycache__", "venv", ".venv", "dist", "build", ".git"}

    def walk(p: Path, depth: int = 0):
        if depth > max_depth:
            return
        try:
            for item in sorted(p.iterdir()):
                if item.name.startswith(".") and item.name != ".":
                    continue
                if item.name in skip:
                    continue
                indent = "  " * depth
                suffix = "/" if item.is_dir() else ""
                lines.append(f"{indent}{item.name}{suffix}")
                if item.is_dir():
                    walk(item, depth + 1)
        except PermissionError:
            pass

    walk(dir_path)
    return lines[:MAX_LIST_LINES]


def _format_result(tc: ToolCall, output: str) -> str:
    """Format tool result with XML wrapper."""
    if tc.name == "grep":
        attrs = f'pattern="{tc.args.get("pattern", "")}"'
        if "sub_dir" in tc.args:
            attrs += f' sub_dir="{tc.args["sub_dir"]}"'
        if "glob" in tc.args:
            attrs += f' glob="{tc.args["glob"]}"'
        return f"<grep {attrs}>\n{output}\n</grep>"
    elif tc.name == "read":
        attrs = f'path="{tc.args.get("path", "")}"'
        if "lines" in tc.args:
            attrs += f' lines="{tc.args["lines"]}"'
        return f"<read {attrs}>\n{output}\n</read>"
    elif tc.name == "list_directory":
        attrs = f'path="{tc.args.get("path", "")}"'
        return f"<list_directory {attrs}>\n{output}\n</list_directory>"
    return output


def search_codebase(
    query: str,
    repo_path: str,
    api_key: str | None = None,
    max_turns: int = MAX_TURNS,
) -> list[dict]:
    """Run the WarpGrep agent loop and return relevant code sections.

    Args:
        query: Natural language search query.
        repo_path: Absolute path to the repository root.
        api_key: Morph API key (falls back to MORPH_API_KEY env var).
        max_turns: Maximum conversation turns (default 4).

    Returns:
        List of dicts with 'path' and 'content' keys.
    """
    key = api_key or os.getenv("MORPH_API_KEY", "")
    if not key:
        return []

    if not Path(repo_path).is_dir():
        return []

    structure = _execute_list_directory(repo_path, ".", None)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"<repo_structure>\n{structure}\n</repo_structure>\n\n"
                f"<search_string>\n{query}\n</search_string>"
            ),
        },
    ]

    for turn in range(max_turns):
        try:
            response = _call_api(messages, key)
        except Exception as e:
            print(f"  WarpGrep API error (turn {turn + 1}): {e}", file=sys.stderr)
            break

        messages.append({"role": "assistant", "content": response})

        tool_calls = _parse_tool_calls(response)
        if not tool_calls:
            break

        # Check for finish
        finish = next((tc for tc in tool_calls if tc.name == "finish"), None)
        if finish:
            return [
                {
                    "path": f["path"],
                    "content": _execute_read(repo_path, f["path"], f.get("lines")),
                }
                for f in finish.args.get("files", [])
            ]

        # Execute tool calls and collect results
        results = []
        for tc in tool_calls:
            if tc.name == "grep":
                out = _execute_grep(
                    repo_path,
                    tc.args.get("pattern", ""),
                    tc.args.get("sub_dir", "."),
                    tc.args.get("glob"),
                )
            elif tc.name == "read":
                out = _execute_read(repo_path, tc.args.get("path", ""), tc.args.get("lines"))
            elif tc.name == "list_directory":
                out = _execute_list_directory(
                    repo_path, tc.args.get("path", "."), tc.args.get("pattern"),
                )
            else:
                out = f"Unknown tool: {tc.name}"
            results.append(_format_result(tc, out))

        remaining = max_turns - turn - 1
        turn_msg = f"\nYou have used {turn + 1} turns and have {remaining} remaining.\n"
        messages.append({"role": "user", "content": "\n\n".join(results) + turn_msg})

    return []


def search_codebase_text(
    query: str,
    repo_path: str,
    api_key: str | None = None,
    max_turns: int = MAX_TURNS,
) -> str:
    """Convenience wrapper that returns results as formatted text."""
    results = search_codebase(query, repo_path, api_key, max_turns)
    if not results:
        return ""

    parts = []
    for r in results:
        parts.append(f"--- {r['path']} ---\n{r['content']}")
    return "\n\n".join(parts)


# --- Anthropic Tool Integration ---

WARPGREP_TOOL_NAME = "warpgrep_codebase_search"
WARPGREP_TOOL_DESCRIPTION = (
    "Your primary investigation tool. Search the codebase using WarpGrep, a code search "
    "subagent that understands code semantics. Use it to answer questions during investigation: "
    "'Who calls this function and how do they handle the return value?', "
    "'How is this shared state accessed across threads?', "
    "'What does this framework API do with empty/nil arguments?', "
    "'Are there other places this pattern is used?'. "
    "For exact string matches (specific variable names, imports), use grep_pattern. "
    "For everything else, use WarpGrep. Investigate aggressively — deeper search finds more real bugs."
)


def create_warpgrep_tool(repo_path: str, api_key: str | None = None) -> dict:
    """Create an Anthropic-compatible tool definition for WarpGrep.

    Register this with the Anthropic SDK's tools parameter so Claude can
    call WarpGrep during its review.

    Args:
        repo_path: Repository root path.
        api_key: Morph API key.

    Returns:
        Tool definition dict for Anthropic messages API.
    """
    return {
        "name": WARPGREP_TOOL_NAME,
        "description": WARPGREP_TOOL_DESCRIPTION,
        "input_schema": {
            "type": "object",
            "properties": {
                "search_string": {
                    "type": "string",
                    "description": (
                        "Natural language query describing what code to find. "
                        "Examples: 'Find where authentication is handled', "
                        "'Definition of UserService class and its callers', "
                        "'How is error handling done in the API layer'"
                    ),
                },
            },
            "required": ["search_string"],
        },
        "_repo_path": repo_path,
        "_api_key": api_key,
    }


def execute_warpgrep_tool(tool_input: dict, tool_def: dict) -> str:
    """Execute a WarpGrep tool call from the Anthropic SDK.

    Args:
        tool_input: The input from Claude's tool_use block.
        tool_def: The tool definition from create_warpgrep_tool().

    Returns:
        Formatted search results as text.
    """
    query = tool_input.get("search_string", "")
    repo_path = tool_def.get("_repo_path", "")
    api_key = tool_def.get("_api_key")

    return search_codebase_text(query, repo_path, api_key)


# Legacy compatibility
class WarpGrepClient:
    """Wrapper class for backward compatibility with existing pipeline code."""

    def __init__(self, api_key: str, base_url: str = API_URL, model: str = MODEL):
        self.api_key = api_key

    def search(self, query: str, repo_path: str, max_turns: int = MAX_TURNS) -> str:
        """Search codebase and return formatted text results."""
        return search_codebase_text(query, repo_path, self.api_key, max_turns)
