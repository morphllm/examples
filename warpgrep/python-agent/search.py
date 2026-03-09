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
from dataclasses import dataclass, field
from pathlib import Path

import requests

# ── Config ──────────────────────────────────────────────────────────────────

MORPH_API_KEY = os.environ.get("MORPH_API_KEY", "")
API_URL = "https://api.morphllm.com/v1/chat/completions"
MODEL = "morph-warp-grep-v2"
MAX_TURNS = 6
MAX_GREP_LINES = 200
MAX_READ_LINES = 800
MAX_CONTEXT_CHARS = 540_000

# ── Types ───────────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    name: str
    args: dict = field(default_factory=dict)


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


# ── Tool Call Parser (Qwen3 format) ─────────────────────────────────────────


def parse_tool_calls(response: str) -> list[ToolCall]:
    """Extract tool calls from the model's Qwen3-format XML response.

    The model emits:
        <tool_call>
        <function=ripgrep>
        <parameter=pattern>value</parameter>
        <parameter=path>.</parameter>
        </function>
        </tool_call>
    """
    clean = re.sub(r"<think>[\s\S]*?</think>", "", response, flags=re.IGNORECASE)
    calls: list[ToolCall] = []

    for match in re.finditer(
        r"<tool_call>\s*<function=([a-z_][a-z0-9_]*)>([\s\S]*?)</function>\s*</tool_call>",
        clean,
        re.IGNORECASE,
    ):
        name = match.group(1).lower()
        body = match.group(2)

        # Extract <parameter=key>value</parameter> pairs
        params: dict[str, str] = {}
        for pm in re.finditer(
            r"<parameter=([a-z_][a-z0-9_]*)>([\s\S]*?)</parameter>",
            body,
            re.IGNORECASE,
        ):
            params[pm.group(1).lower()] = pm.group(2).strip()

        if name == "ripgrep":
            if not params.get("pattern"):
                continue
            calls.append(ToolCall(
                name="grep",
                args={
                    "pattern": params["pattern"],
                    "path": params.get("path", "."),
                    **({"glob": params["glob"]} if "glob" in params else {}),
                },
            ))
        elif name == "list_directory":
            calls.append(ToolCall(
                name="list_directory",
                args={"path": params.get("path", ".")},
            ))
        elif name == "read":
            if not params.get("path"):
                continue
            calls.append(ToolCall(
                name="read",
                args={
                    "path": params["path"],
                    **({"lines": params["lines"]} if "lines" in params else {}),
                },
            ))
        elif name == "finish":
            calls.append(ToolCall(
                name="finish",
                args={
                    "files_raw": params.get("files", ""),
                    "result": params.get("result", ""),
                },
            ))

    return calls


# ── Tool Executors ──────────────────────────────────────────────────────────


def _resolve_path(root: str, path: str) -> str:
    """Resolve a path relative to the repo root, stripping leading slashes."""
    path = path.lstrip("/")
    return str(Path(root) / path)


def run_grep(root: str, pattern: str, path: str = ".", glob: str | None = None) -> str:
    """Run ripgrep and return formatted output."""
    search_path = _resolve_path(root, path)
    cmd = ["rg", "--line-number", "--no-heading", "--color", "never", "-C", "1"]
    if glob:
        cmd.extend(["--glob", glob])
    cmd.extend([pattern, search_path])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=root)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error: {e}"
    output = r.stdout.strip()
    if not output:
        return "no matches"
    # Strip absolute root prefix from grep output paths
    root_prefix = root.rstrip("/") + "/"
    output = output.replace(root_prefix, "")
    lines = output.split("\n")
    if len(lines) > MAX_GREP_LINES:
        return "query not specific enough, tool call tried to return too much context and failed"
    return output


def run_read(root: str, path: str, start: int = 1, end: int | None = None) -> str:
    """Read file contents with optional line range."""
    fp = Path(_resolve_path(root, path))
    if not fp.exists():
        return f"Error: file not found: {path}"
    try:
        all_lines = fp.read_text().splitlines()
    except Exception as e:
        return f"Error: {e}"
    if end is None:
        end = len(all_lines)
    selected = all_lines[start - 1 : end]
    out = [f"{start + i}|{line}" for i, line in enumerate(selected)]
    if len(out) > MAX_READ_LINES:
        out = out[:MAX_READ_LINES] + [f"... truncated ({len(all_lines)} total lines)"]
    return "\n".join(out)


def run_list_dir(root: str, path: str, max_depth: int = 3) -> str:
    """List directory tree with paths relative to repo root."""
    dp = Path(_resolve_path(root, path))
    if not dp.exists():
        return f"Error: directory not found: {path}"
    try:
        r = subprocess.run(
            ["find", str(dp), "-maxdepth", str(max_depth),
             "-not", "-path", "*/.git/*",
             "-not", "-path", "*/node_modules/*",
             "-not", "-path", "*/__pycache__/*"],
            capture_output=True, text=True, timeout=5, cwd=root,
        )
        if not r.stdout.strip():
            return "empty directory"
        # Convert absolute paths to relative paths
        root_prefix = root.rstrip("/") + "/"
        lines = []
        for line in r.stdout.strip().split("\n"):
            if line.startswith(root_prefix):
                lines.append(line[len(root_prefix):])
            elif line == root.rstrip("/"):
                lines.append(".")
            else:
                lines.append(line)
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


# ── Agent Loop ──────────────────────────────────────────────────────────────


def search(query: str, repo_root: str) -> list[dict]:
    """Run the WarpGrep agent loop. Returns list of {path, content} dicts."""
    repo_root = str(Path(repo_root).resolve())

    # Build initial repo structure
    structure = run_list_dir(repo_root, ".")
    budget_str = f"<context_budget>0% (0K/{MAX_CONTEXT_CHARS // 1000}K chars)</context_budget>"
    initial_msg = (
        f"<repo_structure>\n{structure}\n</repo_structure>\n\n"
        f"<search_string>\n{query}\n</search_string>\n"
        f"{budget_str}\n"
        f"Turn 0/{MAX_TURNS}"
    )

    # No system prompt needed: the model uses its native Qwen3 tool format
    messages: list[dict] = [
        {"role": "user", "content": initial_msg},
    ]

    for turn in range(1, MAX_TURNS + 1):
        response = call_api(messages)
        messages.append({"role": "assistant", "content": response})

        calls = parse_tool_calls(response)
        if not calls:
            print(f"  Turn {turn}: no tool calls, stopping")
            break

        # Check for finish
        finish = next((c for c in calls if c.name == "finish"), None)

        # Execute non-finish tools
        results: list[str] = []
        for tc in calls:
            if tc.name == "finish":
                continue
            if tc.name == "grep":
                out = run_grep(repo_root, tc.args["pattern"], tc.args.get("path", "."), tc.args.get("glob"))
            elif tc.name == "read":
                lines_str = tc.args.get("lines")
                if lines_str:
                    ranges = _parse_line_ranges(lines_str)
                    if ranges and len(ranges) == 1:
                        out = run_read(repo_root, tc.args["path"], ranges[0][0], ranges[0][1])
                    elif ranges:
                        chunks = [run_read(repo_root, tc.args["path"], s, e) for s, e in ranges]
                        out = "\n...\n".join(chunks)
                    else:
                        out = run_read(repo_root, tc.args["path"])
                else:
                    out = run_read(repo_root, tc.args["path"])
            elif tc.name == "list_directory":
                out = run_list_dir(repo_root, tc.args.get("path", "."))
            else:
                out = f"Unknown tool: {tc.name}"
            results.append(f"<tool_response>\n{out}\n</tool_response>")

        tool_count = len([c for c in calls if c.name != "finish"])
        print(f"  Turn {turn}: executed {tool_count} tool calls")

        if finish:
            return _resolve_finish(repo_root, finish)

        # Send results back with turn/budget info
        total_chars = sum(len(m["content"]) for m in messages)
        percent = round((total_chars / MAX_CONTEXT_CHARS) * 100)
        used_k = total_chars // 1000
        max_k = MAX_CONTEXT_CHARS // 1000
        remaining = MAX_TURNS - turn

        if remaining <= 1:
            turn_msg = f"You have used {turn} turns, you only have 1 turn remaining. You have run out of turns to explore the code base and MUST call the finish tool now"
        else:
            turn_msg = f"You have used {turn} turn{'s' if turn > 1 else ''} and have {remaining} remaining"

        messages.append({
            "role": "user",
            "content": "\n".join(results) + f"\n{turn_msg}\n<context_budget>{percent}% ({used_k}K/{max_k}K chars)</context_budget>",
        })

    return []


def _parse_line_ranges(lines_str: str) -> list[tuple[int, int]]:
    """Parse line range string like '1-50,75-80' into [(1,50),(75,80)]."""
    ranges: list[tuple[int, int]] = []
    for part in lines_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            pieces = part.split("-", 1)
            try:
                s, e = int(pieces[0]), int(pieces[1])
                ranges.append((s, e))
            except ValueError:
                continue
        else:
            try:
                n = int(part)
                ranges.append((n, n))
            except ValueError:
                continue
    return ranges


def _resolve_finish(root: str, finish: ToolCall) -> list[dict]:
    """Parse finish tool call and read the referenced files."""
    files_raw = finish.args.get("files_raw", "")
    text_result = finish.args.get("result", "")

    # If only a text result, no files
    if text_result and not files_raw:
        print(f"  Finish (text): {text_result[:200]}")
        return []

    if not files_raw:
        return []

    # Parse "path:lines\npath2:*\npath3" format
    results: list[dict] = []
    for line in files_raw.split("\n"):
        line = line.strip()
        if not line:
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            # Whole file
            content = run_read(root, line)
            results.append({"path": line, "content": content})
            continue

        path = line[:colon_idx]
        range_str = line[colon_idx + 1:]

        if range_str.strip() == "*" or not range_str.strip():
            content = run_read(root, path)
            results.append({"path": path, "content": content})
        else:
            ranges = _parse_line_ranges(range_str)
            if ranges:
                chunks = [run_read(root, path, s, e) for s, e in ranges]
                results.append({"path": path, "content": "\n...\n".join(chunks)})
            else:
                content = run_read(root, path)
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
        print(r["content"][:2000])
        if len(r["content"]) > 2000:
            print("  ...(truncated)")
        print()
