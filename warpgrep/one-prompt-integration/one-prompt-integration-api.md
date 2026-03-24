# codebase_search Direct API Integration Prompt

> **What is this?** A prompt you can give to any AI coding assistant (Claude, GPT, Cursor, etc.) to integrate `codebase_search` into an existing agent codebase using the raw HTTP API. Works with **any language** — Python, Go, Rust, Ruby, etc. Copy everything below the line and paste it into your assistant.
>
> **TypeScript/Node.js?** If your agent is written in TypeScript/Node.js, use the [SDK Integration Prompt](./one-prompt-integration-sdk.md) instead — it uses the `@morphllm/morphsdk` package which handles the multi-turn loop for you.

---

## Prompt

You are integrating `codebase_search` — a code search tool — into an existing agent codebase.

**What `codebase_search` is:** A separate LLM sub-agent (powered by the `morph-warp-grep-v2.1` model at `api.morphllm.com`) that takes a **natural-language search string**, searches a codebase using ripgrep, read, list_directory, and glob tools internally, and returns relevant code snippets. It runs in its own context window so it doesn't pollute the parent agent's context. Under the hood, it uses OpenAI-compatible chat completions with native tool calling — you send messages, the model returns structured `tool_calls`, you execute them locally and send results back. Typically 2–6 turns, under 6 seconds. This is an agentic search tool. Not semantic search, not keyword search, not regex search. This tool is intelligent and is capable of reasoning.

**What `codebase_search` is NOT:**
- It is **not** regex search. Do not pass grep patterns to it.
- It is **not** semantic/vector search. It does not use embeddings.
- It is **not** called "WarpGrep", "grep", or "search" from the model's perspective. The tool name visible to the parent model must be `codebase_search`. Models see the word "grep" and assume they need to pass regex patterns. They don't — the input is plain English like "Find the authentication middleware" or "How does the payment flow work?".

**How it works at a high level:**
```
Parent agent receives user question
  → Parent calls codebase_search("How does auth work?")
    → Your code builds a multi-turn loop against api.morphllm.com
    → Model returns tool_calls (grep_search, read, list_directory, glob)
    → You execute those tools locally (ripgrep, file reads, directory listings)
    → You send results back as tool messages
    → Model calls finish with file:line-range specs
    → You read those files and return the content
  → Parent receives { file: "src/auth.py", content: "..." }
  → Parent uses the code context to answer the user
```

**Key difference from the TypeScript SDK:** There is no SDK for your language. You implement the multi-turn agent loop yourself using the raw HTTP API. The API follows the OpenAI chat completions format — if your language has an OpenAI-compatible client library, you can use it pointed at `api.morphllm.com/v1`.

Follow these steps exactly. **At the end of each step, write down what you found and verify it before moving on.** Do not skip ahead.

---

### Step 1: Map the agent architecture

Search the codebase to understand how the agent is structured. You need to answer these questions — write down the answers explicitly before proceeding:

1. **Where is the core agent harness?** Find the main agent loop — the file that sends messages to an LLM and processes responses.
2. **Where are tools created and registered?** Find where tool definitions live and how they're wired into the agent. Look for tool schemas, function declarations, tool execution handlers.
3. **What language and HTTP client does the agent use?** You'll be making HTTP calls to `api.morphllm.com/v1/chat/completions`. Identify what HTTP library is available (e.g., `requests`/`httpx` in Python, `net/http` in Go, `reqwest` in Rust) or whether an OpenAI-compatible client library is available.
4. **What tools does the agent already have?** Look at their schemas and execution patterns — you'll follow the same pattern for `codebase_search`.
5. **How does the agent pass environment variables / secrets?** You'll need `MORPH_API_KEY`.
6. **Is ripgrep installed?** `codebase_search` needs `rg` (ripgrep) available on the system. Check with `which rg` or `rg --version`. If not installed: `brew install ripgrep` (macOS), `apt-get install -y ripgrep` (Debian/Ubuntu), or download from https://github.com/BurntSushi/ripgrep/releases.
7. **Does the agent operate on local code, or a remote sandbox?** The tool executors need filesystem access to the repo being searched.

**Checkpoint:** Before continuing, write down:
- File path of the agent loop: `___`
- File path(s) where tools are defined: `___`
- Language: `___`
- HTTP client / OpenAI client library: `___`
- Existing tool names: `___`
- How env vars are configured: `___`
- ripgrep available: `___`

---

### Step 2: Implement the WarpGrep client (the multi-turn search loop)

This is the core of the integration. You are building a function that:
1. Takes a natural-language query and a repo root path
2. Runs a multi-turn conversation with the `morph-warp-grep-v2.1` model
3. Executes tool calls locally (ripgrep, file reads, directory listings)
4. Returns the found code snippets

The function has four parts: **API client**, **tool executors**, **tool dispatcher**, and **agent loop**. Build them in this order.

#### Part A: API client

The API uses the OpenAI chat completions format. If your language has an OpenAI-compatible client library, use it pointed at `api.morphllm.com/v1`. Otherwise, make raw HTTP POST requests.

**Endpoint:** `POST https://api.morphllm.com/v1/chat/completions`

**Headers:**
```
Authorization: Bearer <MORPH_API_KEY>
Content-Type: application/json
```

**Request body:**
```json
{
  "model": "morph-warp-grep-v2.1",
  "messages": [...],
  "temperature": 0.0,
  "max_tokens": 2048
}
```

**The model has its tools built in — you do NOT pass a `tools` array.** Just send the messages. The model returns standard OpenAI-format `tool_calls` in the response.

**Response format:**
```json
{
  "choices": [{
    "message": {
      "role": "assistant",
      "content": "",
      "tool_calls": [
        {
          "id": "chatcmpl-tool-abc123",
          "type": "function",
          "function": {
            "name": "grep_search",
            "arguments": "{\"pattern\": \"auth.*middleware\", \"path\": \".\", \"glob\": \"*.py\"}"
          }
        }
      ]
    },
    "finish_reason": "tool_calls"
  }]
}
```

When `finish_reason` is `"tool_calls"`, execute the tools and continue the loop. When `finish_reason` is `"stop"` or the model calls `finish`, the search is done.

**Python example using the `openai` library:**

```python
from openai import OpenAI

client = OpenAI(
    api_key=os.environ["MORPH_API_KEY"],
    base_url="https://api.morphllm.com/v1",
)

def call_api(messages: list[dict]):
    response = client.chat.completions.create(
        model="morph-warp-grep-v2.1",
        messages=messages,
        temperature=0.0,
        max_tokens=2048,
    )
    return response.choices[0].message
```

**Python example using raw `requests`:**

```python
import requests

def call_api(messages: list[dict]) -> dict:
    resp = requests.post(
        "https://api.morphllm.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['MORPH_API_KEY']}",
            "Content-Type": "application/json",
        },
        json={
            "model": "morph-warp-grep-v2.1",
            "messages": messages,
            "temperature": 0.0,
            "max_tokens": 2048,
        },
        timeout=45,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]
```

#### Part B: Tool executors

The model calls five tools internally. You must implement each one to execute locally on the filesystem where the repo lives.

**Tool schemas (for reference — you do NOT pass these to the API):**

```python
TOOLS = [
    {
        "name": "grep_search",
        "description": "Search for a regex pattern in file contents. Returns matching lines with file paths and line numbers.",
        "parameters": {
            "pattern": "string (required) — regex pattern",
            "path": "string (optional) — file or directory to search in, defaults to repo root",
            "glob": "string (optional) — glob pattern to filter files, e.g. '*.py'",
            "limit": "integer (optional) — max matching lines"
        }
    },
    {
        "name": "read",
        "description": "Read file contents, optionally a specific line range.",
        "parameters": {
            "path": "string (required) — absolute or relative file path",
            "lines": "string (optional) — line range like '1-50' or '1-20,45-80'"
        }
    },
    {
        "name": "list_directory",
        "description": "List directory structure.",
        "parameters": {
            "command": "string (required) — ls or find command to execute"
        }
    },
    {
        "name": "glob",
        "description": "Find files by name/extension using glob patterns.",
        "parameters": {
            "pattern": "string (required) — glob pattern like '*.py' or 'src/**/*.js'",
            "path": "string (optional) — directory to search in"
        }
    },
    {
        "name": "finish",
        "description": "Submit final answer with file:line-range specs.",
        "parameters": {
            "files": "string (required) — one file per line as path:lines, e.g. 'src/auth.py:1-50\\nsrc/user.py'"
        }
    }
]
```

**Python implementation of all five executors:**

```python
import subprocess
import os
import glob as globmod
from pathlib import Path

MAX_GREP_LINES = 200
MAX_READ_LINES = 800

def execute_grep(repo_root: str, pattern: str, path: str = ".", glob: str | None = None, limit: int | None = None) -> str:
    search_path = path if os.path.isabs(path) else os.path.join(repo_root, path)
    cmd = ["rg", "--line-number", "--no-heading", "--color", "never", "-i", "-C", "1"]
    if glob:
        cmd.extend(["--glob", glob])
    if limit:
        cmd.extend(["--max-count", str(limit)])
    cmd.extend([pattern, search_path])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10, cwd=repo_root)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"Error: {e}"
    output = r.stdout.strip()
    if not output:
        return "no matches"
    # Strip absolute path prefix so output uses relative paths
    root_prefix = repo_root.rstrip("/") + "/"
    output = output.replace(root_prefix, "")
    lines = output.split("\n")
    if len(lines) > MAX_GREP_LINES:
        return "\n".join(lines[:MAX_GREP_LINES]) + f"\n\n... truncated ({len(lines)} lines, limit {MAX_GREP_LINES})"
    return output


def execute_read(repo_root: str, path: str, lines: str | None = None) -> str:
    fp = path if os.path.isabs(path) else os.path.join(repo_root, path)
    if not os.path.exists(fp):
        return f"Error: file not found: {path}"
    try:
        with open(fp, "r", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        return f"Error: {e}"

    if lines and lines != "*":
        selected = []
        for part in lines.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                try:
                    s, e = map(int, part.split("-", 1))
                    selected.extend(
                        f"{i}|{all_lines[i-1].rstrip()}"
                        for i in range(s, min(e + 1, len(all_lines) + 1))
                    )
                except ValueError:
                    continue
            else:
                try:
                    n = int(part)
                    if 1 <= n <= len(all_lines):
                        selected.append(f"{n}|{all_lines[n-1].rstrip()}")
                except ValueError:
                    continue
        if len(selected) > MAX_READ_LINES:
            selected = selected[:MAX_READ_LINES]
            selected.append(f"... truncated ({len(all_lines)} total lines)")
        return "\n".join(selected)

    out = [f"{i+1}|{l.rstrip()}" for i, l in enumerate(all_lines[:MAX_READ_LINES])]
    if len(all_lines) > MAX_READ_LINES:
        out.append(f"... truncated ({len(all_lines)} total lines)")
    return "\n".join(out)


def execute_list_directory(repo_root: str, command: str) -> str:
    try:
        r = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=5, cwd=repo_root
        )
    except (subprocess.TimeoutExpired, Exception) as e:
        return f"Error: {e}"
    output = r.stdout.strip()
    if not output:
        return "empty directory"
    lines = output.split("\n")
    if len(lines) > 200:
        return "\n".join(lines[:200]) + f"\n\n... truncated ({len(lines)} lines)"
    return output


def execute_glob(repo_root: str, pattern: str, path: str | None = None) -> str:
    base = path if path else repo_root
    if not os.path.isabs(base):
        base = os.path.join(repo_root, base)
    full_pattern = os.path.join(base, "**", pattern)
    try:
        matches = sorted(globmod.glob(full_pattern, recursive=True), key=os.path.getmtime, reverse=True)
    except Exception as e:
        return f"Error: {e}"
    if not matches:
        return "no matches"
    # Convert to relative paths
    root_prefix = repo_root.rstrip("/") + "/"
    relative = [m.replace(root_prefix, "") if m.startswith(root_prefix) else m for m in matches[:100]]
    return "\n".join(relative)


def execute_finish(repo_root: str, files_spec: str) -> list[dict]:
    results = []
    for line in files_spec.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        colon_idx = line.find(":")
        if colon_idx == -1:
            content = execute_read(repo_root, line)
            results.append({"file": line, "content": content})
        else:
            path = line[:colon_idx]
            range_str = line[colon_idx + 1:].strip()
            if range_str == "*" or not range_str:
                content = execute_read(repo_root, path)
            else:
                content = execute_read(repo_root, path, range_str)
            results.append({"file": path, "content": content})
    return results
```

**Output limits — enforce these to prevent context explosion:**

| Tool | Max Lines | Behavior on exceed |
|---|---|---|
| `grep_search` | 200 | Truncate with warning |
| `list_directory` | 200 | Truncate with warning |
| `read` | 800 | Truncate with warning |
| `glob` | 100 files | Truncate |

#### Part C: Tool dispatcher

Route tool calls from the model response to the right executor:

```python
import json

def dispatch_tool(name: str, arguments: str, repo_root: str) -> str:
    args = json.loads(arguments)

    if name == "grep_search":
        return execute_grep(
            repo_root,
            args["pattern"],
            args.get("path", "."),
            args.get("glob"),
            args.get("limit"),
        )
    elif name == "read":
        return execute_read(repo_root, args["path"], args.get("lines"))
    elif name == "list_directory":
        return execute_list_directory(repo_root, args.get("command", "ls"))
    elif name == "glob":
        return execute_glob(repo_root, args["pattern"], args.get("path"))
    elif name == "finish":
        # finish is handled specially in the agent loop — return raw
        return args.get("files", "")
    else:
        return f"Unknown tool: {name}"
```

#### Part D: Agent loop

The complete multi-turn loop. This is the function your `codebase_search` tool will call:

```python
MAX_TURNS = 6
MAX_CONTEXT_CHARS = 540_000

def search(query: str, repo_root: str) -> list[dict]:
    """Run the codebase_search agent loop.

    Args:
        query: Natural language search query (e.g. "Find the auth middleware")
        repo_root: Absolute path to the repository root

    Returns:
        List of {"file": "path/to/file.py", "content": "..."} dicts
    """
    repo_root = str(Path(repo_root).resolve())

    # Build initial repo structure (depth 3, excluding junk dirs)
    structure = execute_list_directory(
        repo_root,
        f"find . -maxdepth 3 -not -path '*/.git/*' -not -path '*/node_modules/*' -not -path '*/__pycache__/*'"
    )

    # First message: repo structure + search query
    messages = [
        {
            "role": "user",
            "content": (
                f"<repo_structure>\n{structure}\n</repo_structure>\n\n"
                f"<search_string>\n{query}\n</search_string>"
            ),
        }
    ]

    for turn in range(1, MAX_TURNS + 1):
        msg = call_api(messages)

        # If using openai library: msg is a ChatCompletionMessage object
        # If using raw requests: msg is a dict
        # Normalize to work with both:
        if hasattr(msg, "model_dump"):
            messages.append(msg.model_dump())
            tool_calls = msg.tool_calls or []
        else:
            messages.append(msg)
            tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            break

        # Check for finish tool call
        for tc in tool_calls:
            fn_name = tc.function.name if hasattr(tc, "function") else tc["function"]["name"]
            fn_args = tc.function.arguments if hasattr(tc, "function") else tc["function"]["arguments"]

            if fn_name == "finish":
                args = json.loads(fn_args)
                return execute_finish(repo_root, args.get("files", ""))

        # Execute all non-finish tool calls
        for tc in tool_calls:
            tc_id = tc.id if hasattr(tc, "id") else tc["id"]
            fn_name = tc.function.name if hasattr(tc, "function") else tc["function"]["name"]
            fn_args = tc.function.arguments if hasattr(tc, "function") else tc["function"]["arguments"]

            result = dispatch_tool(fn_name, fn_args, repo_root)
            messages.append({
                "role": "tool",
                "tool_call_id": tc_id,
                "content": result,
            })

        # Add turn counter after all tool results
        remaining = MAX_TURNS - turn
        if remaining <= 1:
            turn_msg = (
                f"You have used {turn} turns, you only have 1 turn remaining. "
                f"You have run out of turns to explore the code base and MUST call the finish tool now"
            )
        else:
            turn_msg = f"You have used {turn} turn{'s' if turn > 1 else ''} and have {remaining} remaining"

        total_chars = sum(len(m.get("content", "") or "") for m in messages)
        percent = round((total_chars / MAX_CONTEXT_CHARS) * 100)
        used_k = total_chars // 1000
        max_k = MAX_CONTEXT_CHARS // 1000

        messages.append({
            "role": "user",
            "content": f"{turn_msg}\n<context_budget>{percent}% ({used_k}K/{max_k}K chars)</context_budget>",
        })

    return []  # exhausted turns without finish
```

**Checkpoint — verify the client works standalone:**

```bash
# Python
MORPH_API_KEY=your-key python -c "
from your_module import search
results = search('Find the main entry point of this project', '.')
print(f'Found {len(results)} files')
for r in results:
    print(f'  {r[\"file\"]} ({len(r[\"content\"])} chars)')
"
```

- [ ] Returns at least 1 file
- [ ] File paths point to real files in the repo
- [ ] Content is actual code, not summaries or empty strings
- [ ] Completes in under 10 seconds

If this fails: check `MORPH_API_KEY`, check ripgrep is installed (`rg --version`), check network access to `api.morphllm.com`.

---

### Step 3: Verify that tool executors work independently

Before wiring anything into the agent, verify that each tool executor works correctly on your filesystem.

```python
# Save as test-executors.py, run with: python test-executors.py

import os

repo_root = os.getcwd()

# Test grep
print("=== grep_search ===")
grep_result = execute_grep(repo_root, "import", ".", "*.py")
print(f"  Length: {len(grep_result)} chars")
print(f"  Has matches: {'no matches' not in grep_result}")

# Test read
print("\n=== read ===")
# Find any file to read
for f in os.listdir(repo_root):
    if os.path.isfile(f):
        read_result = execute_read(repo_root, f, "1-10")
        print(f"  File: {f}")
        print(f"  Lines: {len(read_result.splitlines())}")
        break

# Test list_directory
print("\n=== list_directory ===")
ls_result = execute_list_directory(repo_root, "find . -maxdepth 2 -type f")
print(f"  Files found: {len(ls_result.splitlines())}")

# Test glob
print("\n=== glob ===")
glob_result = execute_glob(repo_root, "*.py")
print(f"  Python files: {len(glob_result.splitlines()) if glob_result != 'no matches' else 0}")

print("\nAll executors OK" if all([
    "no matches" not in grep_result or True,  # grep may legitimately find nothing
    "Error" not in read_result,
    "Error" not in ls_result,
]) else "\nSome executors FAILED — fix before continuing")
```

**Checkpoint — verify all of these pass:**
- [ ] `grep_search` runs without errors (ripgrep is installed and works)
- [ ] `read` returns actual file content with line numbers
- [ ] `list_directory` returns file/directory listings
- [ ] `glob` finds files by pattern
- [ ] No `Error:` prefixes in any output

If `grep_search` fails with "command not found": install ripgrep (`brew install ripgrep` / `apt-get install -y ripgrep`).

---

### Step 4: Create the `codebase_search` tool and wire it into the agent

Now wrap the `search()` function as a tool the parent agent can call. The wrapper is thin — it takes a natural-language query, calls `search()`, and formats the results as a string.

**The formatting function:**

```python
def format_search_results(results: list[dict]) -> str:
    if not results:
        return "No relevant code found."
    parts = []
    for r in results:
        parts.append(f"--- {r['file']} ---\n{r['content']}")
    return "\n\n".join(parts)
```

Before you move on to the integration, think about codebase best practices. Where are tools commonly defined? If there is no existing pattern for tools, design an abstraction layer that improves the codebase.

#### Step 4a: Identify the agent harness type

The agent you're integrating into uses one of three harness patterns. **This determines everything about how you wire in the search function.** Look at what you found in Step 1 — specifically how tools are defined, registered, and invoked — and classify it:

**Type 1: Tool-Use Loop (direct LLM API call)**

The agent calls an LLM provider API directly (OpenAI, Anthropic, Google, etc.) and has its own tool-calling loop. You'll see code like:
- `client.chat.completions.create(tools=[...])` or `client.messages.create(tools=[...])`
- A loop that checks `finish_reason` / `stop_reason` and processes `tool_calls` / `tool_use` blocks
- Tool definitions as dicts/JSON schemas passed to the LLM call

**How to detect:** Look for direct imports of `openai`, `anthropic`, `google.genai`, or similar. The agent manages its own message history and tool execution loop.

→ **If this is your agent:** Proceed to **Step 4b** below.

---

**Type 2: Agent Framework with its own Tool abstraction**

The agent uses a framework that has its own tool interface — a class, decorator, or factory function. You'll see code like:
- `class MyTool(BaseTool)` with a `_run()` method (crewAI, LangChain)
- `@tool` decorators or `StructuredTool` subclasses
- `Tool.define("name", ...)` patterns
- MCP `list_tools()` / `call_tool()` implementations

**How to detect:** Look for framework-specific base classes, decorators, or factory functions. The framework — not your code — manages the LLM loop and tool dispatch.

→ **If this is your agent:** Proceed to **Step 4c**.

---

**Type 3: CLI / Bash agent**

The agent operates by emitting shell commands, and tools are configured as executables with command signatures. You'll see code like:
- `tools:` config blocks with `signature: "toolname <arg>"` and `docstring: "..."`
- A bash execution harness that runs commands and captures stdout
- No programmatic tool interface

**How to detect:** Look for YAML/JSON tool configs with `signature` fields, or a harness that pipes model output through a shell.

→ **If this is your agent:** Proceed to **Step 4d**.

---

**Checkpoint:** Before continuing, write down:
- Harness type: `Tool-Use Loop` / `Agent Framework` / `CLI/Bash`
- Evidence (what you saw that told you): `___`

---

#### Step 4b: Tool-Use Loop integration (direct LLM API)

Register `codebase_search` as a tool alongside the agent's existing tools. Follow the agent's existing pattern.

**Tool schema (use this for all providers):**

```python
CODEBASE_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "codebase_search",
        "description": (
            "Search the codebase using natural language. Input is a plain English "
            "description of what you're looking for, NOT regex. Returns matching "
            "code snippets with file paths."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Plain English description of what to search for",
                }
            },
            "required": ["search_term"],
        },
    },
}
```

**Adapt the schema format for your provider:**

<details>
<summary>OpenAI / OpenAI-compatible</summary>

Use `CODEBASE_SEARCH_TOOL` as-is — it's already in OpenAI format.

```python
from openai import OpenAI

client = OpenAI()
tools = [CODEBASE_SEARCH_TOOL, ...existing_tools...]

messages = [{"role": "user", "content": "How does the auth middleware work?"}]

for turn in range(5):
    response = client.chat.completions.create(
        model="gpt-4o",
        tools=tools,
        messages=messages,
    )
    choice = response.choices[0]

    if choice.finish_reason == "stop":
        print(choice.message.content)
        break

    messages.append(choice.message.model_dump())

    for tc in choice.message.tool_calls or []:
        if tc.function.name == "codebase_search":
            args = json.loads(tc.function.arguments)
            results = search(args["search_term"], "/path/to/repo")
            content = format_search_results(results)
        else:
            content = handle_other_tool(tc)  # your existing tool handler

        messages.append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": content,
        })
```

</details>

<details>
<summary>Anthropic</summary>

Anthropic uses `input_schema` instead of `parameters`:

```python
import anthropic

client = anthropic.Anthropic()

tools = [
    {
        "name": "codebase_search",
        "description": (
            "Search the codebase using natural language. Input is plain English, NOT regex."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search_term": {
                    "type": "string",
                    "description": "Plain English description of what to search for",
                }
            },
            "required": ["search_term"],
        },
    },
    ...existing_tools...
]

messages = [{"role": "user", "content": "How does the auth middleware work?"}]

for turn in range(5):
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        tools=tools,
        messages=messages,
    )

    messages.append({"role": "assistant", "content": response.content})

    if response.stop_reason == "end_turn":
        for block in response.content:
            if hasattr(block, "text"):
                print(block.text)
        break

    tool_results = []
    for block in response.content:
        if block.type == "tool_use":
            if block.name == "codebase_search":
                results = search(block.input["search_term"], "/path/to/repo")
                content = format_search_results(results)
            else:
                content = handle_other_tool(block)

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": content,
            })

    if tool_results:
        messages.append({"role": "user", "content": tool_results})
```

</details>

<details>
<summary>Google Gemini</summary>

```python
from google import genai
from google.genai import types

client = genai.Client()

tool = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="codebase_search",
            description="Search the codebase using natural language. Input is plain English, NOT regex.",
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "search_term": types.Schema(
                        type="STRING",
                        description="Plain English description of what to search for",
                    )
                },
                required=["search_term"],
            ),
        ),
        ...existing_tools...
    ]
)

response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents="How does the auth middleware work?",
    config=types.GenerateContentConfig(tools=[tool]),
)

fc = response.candidates[0].content.parts[0].function_call
if fc.name == "codebase_search":
    results = search(fc.args["search_term"], "/path/to/repo")
    result_text = format_search_results(results)

    final = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            types.Content(role="user", parts=[types.Part.from_text(text="How does the auth middleware work?")]),
            response.candidates[0].content,
            types.Content(
                role="user",
                parts=[types.Part.from_function_response(name="codebase_search", response={"result": result_text})],
            ),
        ],
        config=types.GenerateContentConfig(tools=[tool]),
    )
    print(final.text)
```

</details>

Then skip to **Step 5**.

---

#### Step 4c: Agent Framework integration (framework-specific tool wrapper)

Define a tool that conforms to the framework's interface. The execute body calls your `search()` function.

**The pattern is always the same:**

1. Import your `search()` and `format_search_results()` functions
2. Define a tool using the framework's interface (subclass, decorator, factory — whatever it uses)
3. In the execute/run method, call `search()` and return the formatted result

**Generic template — adapt to your framework:**

```python
# name: "codebase_search"  ← MUST be this name
# description: "Search the codebase using natural language. Input is plain English, NOT regex."
# parameters: { search_term: string }  ← single required string parameter

def execute_codebase_search(search_term: str, repo_root: str = ".") -> str:
    results = search(search_term, repo_root)
    return format_search_results(results)
```

**Concrete examples for common frameworks:**

<details>
<summary>LangChain / crewAI (BaseTool subclass)</summary>

```python
from langchain.tools import BaseTool

class CodebaseSearchTool(BaseTool):
    name = "codebase_search"
    description = "Search the codebase using natural language. Input is plain English, NOT regex."
    repo_root: str = "."

    def _run(self, search_term: str) -> str:
        results = search(search_term, self.repo_root)
        return format_search_results(results)
```

</details>

<details>
<summary>LangChain @tool decorator</summary>

```python
from langchain.tools import tool

@tool
def codebase_search(search_term: str) -> str:
    """Search the codebase using natural language. Input is plain English, NOT regex."""
    results = search(search_term, "/path/to/repo")
    return format_search_results(results)
```

</details>

<details>
<summary>MCP server (list_tools / call_tool)</summary>

```python
# In your MCP server's list_tools handler:
{
    "name": "codebase_search",
    "description": "Search the codebase using natural language. Input is plain English, NOT regex.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "search_term": {
                "type": "string",
                "description": "Plain English description of what to search for",
            },
        },
        "required": ["search_term"],
    },
    "annotations": {
        "readOnlyHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
}

# In your MCP server's call_tool handler:
if tool_name == "codebase_search":
    results = search(args["search_term"], repo_root)
    text = format_search_results(results)
    return {"content": [{"type": "text", "text": text}]}
```

</details>

**Key rules:**
- The tool name **must** be `codebase_search`. If the model sees "grep" in the name, it will pass regex patterns instead of English.
- The description **must** say "natural language" or "plain English".
- There is only one parameter: `search_term` (string).

**Checkpoint:** After wiring it in:
- [ ] The tool appears in the framework's tool registry with name `codebase_search`
- [ ] A test invocation returns real file content (not errors, not empty)
- [ ] The result is a plain string

Then skip to **Step 5**.

---

#### Step 4d: CLI / Bash integration (executable wrapper)

Write a CLI script that wraps your `search()` function and register it as a shell command.

**1. Create the CLI wrapper:**

Save as `bin/codebase_search` and make it executable (`chmod +x`):

```python
#!/usr/bin/env python3
"""CLI wrapper for codebase_search."""
import os
import sys

# Adjust this import to wherever you put the search function
from your_module import search, format_search_results

query = " ".join(sys.argv[1:])
if not query:
    print("Usage: codebase_search <natural language query>", file=sys.stderr)
    print('Example: codebase_search "Find the authentication middleware"', file=sys.stderr)
    sys.exit(1)

if not os.environ.get("MORPH_API_KEY"):
    print("MORPH_API_KEY environment variable is required", file=sys.stderr)
    sys.exit(1)

results = search(query, os.environ.get("REPO_ROOT", os.getcwd()))
print(format_search_results(results))
```

**2. Register it in the agent's tool config:**

```yaml
tools:
  codebase_search:
    signature: "codebase_search <query>"
    docstring: |
      Search the codebase using natural language. Takes a plain English description
      of what you're looking for (NOT regex). Returns matching file contents.
      Example: codebase_search "Find the authentication middleware"
      Example: codebase_search "How does the payment flow work?"
```

**3. Test it:**

```bash
MORPH_API_KEY=your-key ./bin/codebase_search "Find the main entry point"
```

**Checkpoint:** After wiring it in:
- [ ] `./bin/codebase_search "test query"` returns real file content on stdout
- [ ] The tool appears in the agent's tool config with name `codebase_search`

Then proceed to **Step 5**.

---

### Step 5: Add system prompt guidance

Append the following to the agent's existing system prompt so the parent model knows when to use the tool. **Do not remove or rewrite existing system prompt content — only append.**

```
## codebase_search — when to use

USE codebase_search when you need to:
- Explore unfamiliar parts of the codebase
- Find implementations across multiple files (e.g., "Find the auth middleware", "Where is the payment flow?")
- Understand how a feature or system works before making changes
- Locate code by description rather than by exact name
- Find related code, callers, or dependencies of a function/class

DO NOT use codebase_search when:
- You already know the exact file and line (just read the file directly)
- You need a simple string/regex match on a known pattern (use grep directly)
- You're searching for a single known symbol name (use grep directly)

IMPORTANT: codebase_search takes plain English, not regex. Write queries like you'd describe what you're looking for to another engineer.

Best practice: Use codebase_search at the START of a task to orient yourself, then use direct file reads/grep for targeted follow-ups.
```

---

### Step 6: Test incrementally

Do not skip layers. Each test verifies a different part of the stack. If a layer fails, fix it before moving on — problems compound.

#### Layer 1 — API key and network connectivity

```python
# Save as test-1-api.py, run with: MORPH_API_KEY=your-key python test-1-api.py
import os
import requests

api_key = os.environ.get("MORPH_API_KEY")
if not api_key:
    print("FAIL: Set MORPH_API_KEY — get one from https://morphllm.com/dashboard/api-keys")
    exit(1)

# Test connectivity
resp = requests.get(
    "https://api.morphllm.com/v1/models",
    headers={"Authorization": f"Bearer {api_key}"},
    timeout=10,
)
print(f"API reachable: {'OK' if resp.status_code == 200 else 'FAIL (' + str(resp.status_code) + ')'}")

# Test auth
if resp.status_code == 401:
    print("FAIL: API key is invalid")
elif resp.status_code == 200:
    print("API key valid: OK")
```

**Expected:** Both OK. If network error → `api.morphllm.com` is blocked. If 401 → bad API key.

#### Layer 2 — ripgrep works

```bash
rg --version
# Should print ripgrep version. If not found, install it.

rg --line-number --no-heading "import" . --glob "*.py" | head -5
# Should show matching lines from Python files
```

#### Layer 3 — Tool executors work

Run the executor test from Step 3. All should pass without errors.

#### Layer 4 — Full search loop works standalone

```python
# Save as test-4-search.py, run with: MORPH_API_KEY=your-key python test-4-search.py
results = search("Find the main entry point of this project", ".")

print(f"success: {len(results) > 0}")
print(f"files found: {len(results)}")

for r in results:
    print(f"  {r['file']} ({len(r['content'])} chars)")

# Sanity checks
has_file_paths = any("/" in r["file"] or "." in r["file"] for r in results)
has_content = any(len(r["content"]) > 20 for r in results)
print(f"\nSanity checks:")
print(f"  Has file paths: {'PASS' if has_file_paths else 'FAIL'}")
print(f"  Has real content: {'PASS' if has_content else 'FAIL'}")
```

**Expected:** At least 1 file found. Content is real code, not summaries.

#### Layer 5 — Real agent end-to-end

Trigger the agent with a prompt that requires code search:

> "Use codebase_search to find how errors are handled in this project, then summarize what you found."

**Verify each of these explicitly:**

1. **Does the agent call `codebase_search`?** (not grep, cat, find, or any other tool)
2. **Is the query natural language?** The `search_term` should read like English, not regex.
3. **Does the search succeed?** Check for non-empty results.
4. **Does the returned context contain real code?** File paths and actual code snippets.
5. **Does the agent use the context in its response?** References specific files, functions, or code patterns.

**If the model passes regex instead of English:** The system prompt from Step 5 is missing or the tool name contains "grep". Double-check both.

---

### Step 7: Sandbox / remote execution (optional — only if code is in a remote sandbox)

**Skip this step if the agent and the repo are on the same machine.** Local execution (the default) already works — Step 3 verified it.

If your agent runs code in a sandbox (E2B, Modal, Docker, SSH, etc.), the tool executors need to run commands **inside the sandbox** instead of locally. Replace the subprocess calls in each executor with your sandbox's execution API.

**Prerequisites:** `ripgrep` must be installed inside the sandbox.

```python
# Replace local subprocess calls with sandbox execution

def execute_grep_remote(sandbox, repo_root, pattern, path=".", glob=None):
    glob_arg = f" --glob '{glob}'" if glob else ""
    result = sandbox.commands.run(
        f"rg --line-number --no-heading --color never -i -C 1 '{pattern}' '{path}'{glob_arg}",
        cwd=repo_root,
    )
    return result.stdout or "no matches"

def execute_read_remote(sandbox, repo_root, path, lines=None):
    if lines:
        result = sandbox.commands.run(f"sed -n '{lines}p' '{path}'", cwd=repo_root)
    else:
        result = sandbox.commands.run(f"cat '{path}'", cwd=repo_root)
    return result.stdout or f"Error: file not found: {path}"

def execute_list_directory_remote(sandbox, repo_root, command):
    result = sandbox.commands.run(command, cwd=repo_root)
    return result.stdout or "empty directory"
```

Adapt `sandbox.commands.run(...)` to whatever execution API your sandbox provides.

---

### Step 8: Production hardening (optional)

#### Retry logic for transient errors

The API may return 429 (rate limit) or 5xx (server errors). Add retry with exponential backoff:

```python
import time

MAX_RETRIES = 3
RETRY_BASE_DELAY = 3.0  # seconds

def call_api_with_retry(messages: list[dict]) -> dict:
    for attempt in range(MAX_RETRIES):
        try:
            return call_api(messages)
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                raise
            delay = RETRY_BASE_DELAY * (2 ** attempt)
            print(f"API error, retrying in {delay:.0f}s: {e}")
            time.sleep(delay)
```

#### Concurrency limiting

If multiple searches can run in parallel, limit concurrency to avoid rate limiting:

```python
import threading

_api_semaphore = threading.Semaphore(4)  # max 4 concurrent searches

def search_with_limit(query: str, repo_root: str) -> list[dict]:
    _api_semaphore.acquire()
    try:
        return search(query, repo_root)
    finally:
        _api_semaphore.release()
```

#### Context budget enforcement

Guard against context explosion if tool results are very large:

```python
# Inside the agent loop, before appending tool results:
total_chars = sum(len(m.get("content", "") or "") for m in messages)
tool_result_chars = len(result)

if total_chars + tool_result_chars > MAX_CONTEXT_CHARS:
    budget = MAX_CONTEXT_CHARS - total_chars - 200
    if budget > 0:
        result = result[:budget] + "\n\n... truncated to fit context budget"
    else:
        result = "Tool results too large, skipped. Try more specific queries."
```

---

### Reference Documentation

- **Direct API Protocol:** https://docs.morphllm.com/sdk/components/warp-grep/direct
- **Python Agent Example:** https://github.com/morphllm/examples/tree/main/warpgrep/python-agent
- **TypeScript SDK (Node.js only):** `npm install @morphllm/morphsdk` ([npm](https://www.npmjs.com/package/@morphllm/morphsdk))
- **SDK Integration Prompt (TypeScript):** [one-prompt-integration-sdk.md](./one-prompt-integration-sdk.md)
- **Examples (all providers + sandboxes):** https://github.com/morphllm/examples/tree/main/warpgrep
- **API Keys:** https://morphllm.com/dashboard/api-keys
- **Pricing:** $0.80 / 1M tokens input, $0.80 / 1M tokens output
