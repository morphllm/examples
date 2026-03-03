"""
WarpGrep Python Agent

A complete, self-contained Python implementation of WarpGrep.
No SDK needed — just requests + ripgrep.

Usage:
    MORPH_API_KEY=your-key python search.py
    MORPH_API_KEY=your-key python search.py "your query" /path/to/repo
"""

import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────

MORPH_API_KEY = os.environ.get("MORPH_API_KEY", "")
API_URL = "https://api.morphllm.com/v1/chat/completions"
MODEL = "morph-warp-grep-v2"
MAX_TURNS = 4
MAX_GREP_LINES = 200
MAX_READ_LINES = 800

# ── Types ───────────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    name: str
    args: dict[str, str]


# ── API Client ──────────────────────────────────────────────────────────────


def call_api(messages: list[dict]) -> str:
    """Call the WarpGrep model and return the response text."""
    resp = requests.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {MORPH_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 2048,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]


# ── XML Parser ──────────────────────────────────────────────────────────────


def parse_tool_calls(response: str) -> list[ToolCall]:
    """Extract tool calls from the model's XML response."""
    response = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL)
    calls = []
    for name in ("grep", "read", "list_directory", "finish"):
        for match in re.finditer(rf"<{name}>(.*?)</{name}>", response, re.DOTALL):
            args = _parse_elements(match.group(1))
            calls.append(ToolCall(name=name, args=args))
    return calls


def _parse_elements(xml: str) -> dict:
    """Parse <key>value</key> pairs from XML content."""
    args: dict = {}
    for match in re.finditer(r"<(\w+)>(.*?)</\1>", xml, re.DOTALL):
        key, val = match.group(1), match.group(2).strip()
        if key == "file":
            args.setdefault("files", []).append(_parse_elements(val))
        else:
            args[key] = val
    return args


# ── Tool Executors ──────────────────────────────────────────────────────────


def run_grep(root: str, pattern: str, sub_dir: str = ".", glob: str | None = None) -> str:
    """Run ripgrep and return formatted output."""
    cmd = ["rg", "--line-number", "--no-heading", "--color", "never", "-C", "1"]
    if glob:
        cmd.extend(["--glob", glob])
    cmd.extend([pattern, str(Path(root) / sub_dir)])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=root)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error: {e}"
    lines = r.stdout.strip().split("\n") if r.stdout.strip() else []
    if len(lines) > MAX_GREP_LINES:
        return "query not specific enough, tool call tried to return too much context and failed"
    return r.stdout.strip() or "no matches"


def run_read(root: str, path: str, lines: str | None = None) -> str:
    """Read file contents with optional line ranges."""
    fp = Path(root) / path
    if not fp.exists():
        return f"Error: file not found: {path}"
    try:
        all_lines = fp.read_text().splitlines()
    except Exception as e:
        return f"Error: {e}"
    if lines:
        selected = []
        for part in lines.split(","):
            if "-" in part:
                s, e = map(int, part.split("-"))
                selected.extend(range(s - 1, min(e, len(all_lines))))
            else:
                selected.append(int(part) - 1)
        out = [f"{i + 1}|{all_lines[i]}" for i in sorted(set(selected)) if 0 <= i < len(all_lines)]
    else:
        out = [f"{i + 1}|{l}" for i, l in enumerate(all_lines)]
    if len(out) > MAX_READ_LINES:
        out = out[:MAX_READ_LINES] + [f"... truncated ({len(all_lines)} total lines)"]
    return "\n".join(out)


def run_list_dir(root: str, path: str) -> str:
    """List directory tree."""
    dp = Path(root) / path
    if not dp.exists():
        return f"Error: directory not found: {path}"
    try:
        r = subprocess.run(
            ["find", str(dp), "-maxdepth", "3", "-not", "-path", "*/.git/*", "-not", "-path", "*/node_modules/*"],
            capture_output=True, text=True, timeout=5, cwd=root,
        )
        return r.stdout.strip() or "empty directory"
    except Exception as e:
        return f"Error: {e}"


# ── Result Formatter ────────────────────────────────────────────────────────


def format_result(tc: ToolCall, output: str) -> str:
    """Wrap tool output in XML tags for the model."""
    if tc.name == "grep":
        attrs = f'pattern="{tc.args.get("pattern", "")}"'
        if "sub_dir" in tc.args:
            attrs += f' sub_dir="{tc.args["sub_dir"]}"'
        return f"<grep {attrs}>\n{output}\n</grep>"
    if tc.name == "read":
        attrs = f'path="{tc.args.get("path", "")}"'
        return f"<read {attrs}>\n{output}\n</read>"
    if tc.name == "list_directory":
        return f'<list_directory path="{tc.args.get("path", "")}">\n{output}\n</list_directory>'
    return output


# ── System Prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = r"""You are a code search agent. Your task is to find all relevant code for a given search_string.

### workflow
You have exactly 4 turns. The 4th turn MUST be a `finish` call. Each turn allows up to 8 parallel tool calls.
- Turn 1: Map the territory OR dive deep (based on search_string specificity)
- Turn 2-3: Refine based on findings
- Turn 4: MUST call `finish` with all relevant code locations
- You MAY call `finish` early if confident—but never before at least 1 search turn.

### tools
Tool calls use nested XML elements:

### `grep`
- `<pattern>` (required): Regex pattern
- `<sub_dir>` (optional): Subdirectory to search
- `<glob>` (optional): File filter like `*.py`

### `read`
- `<path>` (required): File path
- `<lines>` (optional): Line ranges like "1-50,75-80"

### `list_directory`
- `<path>` (required): Directory path

### `finish`
- `<file>` elements with `<path>` and optional `<lines>`

<output_format>
EVERY response MUST:
1. Wrap reasoning in `<think>...</think>` tags
2. Then output up to 8 tool calls as XML
</output_format>
"""


# ── Agent Loop ──────────────────────────────────────────────────────────────


def search(query: str, repo_root: str) -> list[dict]:
    """Run the WarpGrep agent loop. Returns list of {path, content} dicts."""
    # Build initial repo structure
    structure = run_list_dir(repo_root, ".")
    initial_msg = f"<repo_structure>\n{structure}\n</repo_structure>\n\n<search_string>\n{query}\n</search_string>"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": initial_msg},
    ]

    for turn in range(MAX_TURNS):
        response = call_api(messages)
        messages.append({"role": "assistant", "content": response})

        calls = parse_tool_calls(response)
        if not calls:
            break

        # Check for finish
        finish = next((c for c in calls if c.name == "finish"), None)
        if finish:
            return _resolve_finish(repo_root, finish)

        # Execute tools and send results back
        results = []
        for tc in calls:
            if tc.name == "grep":
                out = run_grep(repo_root, tc.args.get("pattern", ""), tc.args.get("sub_dir", "."), tc.args.get("glob"))
            elif tc.name == "read":
                out = run_read(repo_root, tc.args.get("path", ""), tc.args.get("lines"))
            elif tc.name == "list_directory":
                out = run_list_dir(repo_root, tc.args.get("path", "."))
            else:
                out = f"Unknown tool: {tc.name}"
            results.append(format_result(tc, out))

        remaining = MAX_TURNS - turn - 1
        turn_msg = f"You have used {turn + 1} turns and have {remaining} remaining."
        if remaining <= 1:
            turn_msg = "You have run out of turns and MUST call the finish tool now."

        messages.append({"role": "user", "content": "\n\n".join(results) + f"\n\n{turn_msg}"})
        print(f"  Turn {turn + 1}: executed {len(calls)} tool calls")

    return []


def _resolve_finish(root: str, finish: ToolCall) -> list[dict]:
    """Read files from a finish call."""
    results = []
    for f in finish.args.get("files", []):
        path = f.get("path", "")
        lines = f.get("lines")
        if lines == "*":
            lines = None
        content = run_read(root, path, lines)
        results.append({"path": path, "content": content})
    return results


# ── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not MORPH_API_KEY:
        print("Error: set MORPH_API_KEY environment variable")
        sys.exit(1)

    query = sys.argv[1] if len(sys.argv) > 1 else "Find the main entry point of this project"
    repo = sys.argv[2] if len(sys.argv) > 2 else "."

    print(f'Searching for: "{query}" in {repo}\n')
    results = search(query, repo)

    if not results:
        print("No results found.")
        sys.exit(1)

    print(f"\nFound {len(results)} relevant files:\n")
    for r in results:
        print(f"--- {r['path']} ---")
        print(r["content"][:1000])
        if len(r["content"]) > 1000:
            print("  ...(truncated)")
        print()
