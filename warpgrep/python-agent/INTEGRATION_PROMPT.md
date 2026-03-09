# WarpGrep Python Integration Prompt

> **What is this?** A universal prompt you can give to any AI coding assistant (Claude, GPT, Cursor, etc.) to integrate WarpGrep into an existing Python agent codebase. Copy everything below the line and paste it into your assistant.

---

## Prompt

You are integrating WarpGrep — a code search sub-agent — into an existing Python agent codebase. WarpGrep is NOT a regex tool. It is a separate LLM that takes a **natural-language query**, searches a codebase using ripgrep/file-reads internally, and returns relevant code snippets. It runs in its own context window so it doesn't pollute the parent agent's context.

**Important naming convention:** Name the tool `codebase_search`, NOT `warpgrep` or `grep`. Parent models see the word "grep" and assume they need to pass regex patterns. They don't — the input is plain English like "Find the authentication middleware" or "How does the payment flow work?".

Follow these steps exactly. Complete each step before moving to the next.

---

### Step 1: Identify the core agent harness

Search the codebase for the main agent loop. Look for:
- A conversation loop that sends messages to an LLM and processes responses
- Tool/function calling handling (parsing tool calls from model responses, executing them, sending results back)
- The main entry point file (usually `main.py`, `agent.py`, `app.py`, or similar)

Identify: What file contains the agent loop? What LLM provider does it use (OpenAI, Anthropic, Gemini, LangChain, etc.)?

### Step 2: Find how the agent registers and executes tools

Search for how tools are defined and added. Look for:
- Tool definitions / function declarations (JSON schemas, decorator-based tools, etc.)
- Tool execution handlers (where tool call results are processed and sent back to the model)
- The pattern used: Is it OpenAI function calling? Anthropic tool_use blocks? LangChain tools? A custom implementation?

### Step 3: Check for existing tools

Search the codebase for any tools already registered with the agent. This tells you the exact pattern to follow. Look for:
- Tool name strings, tool description constants
- Tool schema definitions (JSON Schema objects, Pydantic models, etc.)
- Tool execution dispatch (if/elif chains, dictionaries mapping tool names to handlers)

### Step 4: Add the WarpGrep codebase_search module

Create a new file (e.g., `codebase_search.py` or `warpgrep_tool.py`) in the appropriate location. This file contains the WarpGrep agent loop — 4 internal functions that WarpGrep uses to search code, plus the main `search()` function that orchestrates them.

The module needs these components. Copy them from the reference implementation at:
https://github.com/morphllm/examples/tree/main/warpgrep/python-agent

**Reference: `search.py`** — The complete self-contained implementation (~390 lines). It contains:

1. **`call_api(messages)`** — Calls the WarpGrep model (`morph-warp-grep-v2`) at `https://api.morphllm.com/v1/chat/completions`
2. **`parse_tool_calls(response)`** — Parses XML tool calls from model responses
3. **Four internal tool executors** (these are WarpGrep's internal tools, not your agent's tools):
   - `run_grep(root, pattern, path, glob)` — Runs ripgrep locally
   - `run_read(root, path, start, end)` — Reads file contents with line ranges
   - `run_list_dir(root, path)` — Lists directory structure
   - `_resolve_finish(root, finish)` — Resolves the `finish` tool call into final results
4. **`search(query, repo_root)`** — The main agent loop (max 6 turns). This is what your parent agent calls.

The `search()` function takes a natural-language query and a repo path, returns `list[dict]` where each dict has `{"path": "...", "content": "..."}`.

**Requirements:**
- `pip install requests`
- `ripgrep` must be installed (`brew install ripgrep` / `apt-get install ripgrep`)
- `MORPH_API_KEY` environment variable (get from https://morphllm.com/dashboard/api-keys)

### Step 5: Register `codebase_search` as a tool in your agent

Add the tool to your agent's tool list following the pattern from Step 3. The tool schema is minimal — just one required string parameter:

**Tool definition:**
```
name: codebase_search
description: Search the codebase for relevant code. Takes a natural-language query describing what you're looking for (e.g., "Find the authentication middleware", "How does error handling work in the API layer"). Returns matching file snippets with paths and line numbers. Use this to explore unfamiliar code, find implementations across multiple files, or understand how a feature works. Do NOT pass regex patterns — use plain English.
parameters:
  query (string, required): Natural-language description of what code to find
```

**Tool execution handler:**
When the parent model calls `codebase_search`, execute it like this:
```python
from codebase_search import search  # adjust import

def handle_codebase_search(query: str, repo_root: str = ".") -> str:
    results = search(query, repo_root)
    if not results:
        return "No relevant code found."
    parts = []
    for r in results:
        parts.append(f"--- {r['path']} ---\n{r['content']}")
    return "\n\n".join(parts)
```

Wire this into your agent's tool dispatch alongside any existing tools.

**Provider-specific examples:** See `search_tool.py` in the same repo for complete working examples for OpenAI, Anthropic, and Gemini:
https://github.com/morphllm/examples/blob/main/warpgrep/python-agent/search_tool.py

### Step 6: Add system prompt guidance

Append the following to the agent's system prompt (or tool description) so the parent model knows when to use the tool:

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

Best practice: Use codebase_search at the START of a task to orient yourself, then use direct file reads/grep for targeted follow-ups.
```

### Step 7: Test the integration

Run these tests in order. Each one verifies a different layer.

**Test A — API key validation:**
```python
import os, requests
resp = requests.post(
    "https://api.morphllm.com/v1/chat/completions",
    headers={"Authorization": f"Bearer {os.environ['MORPH_API_KEY']}", "Content-Type": "application/json"},
    json={"model": "morph-warp-grep-v2", "messages": [{"role": "user", "content": "<repo_structure>\ntest/\n</repo_structure>\n\n<search_string>\ntest\n</search_string>"}], "temperature": 0.0, "max_tokens": 256},
    timeout=15,
)
print(f"Status: {resp.status_code}")
print(f"Response: {resp.json()['choices'][0]['message']['content'][:200]}")
# Expected: 200 status, response with <tool_call> XML
```

**Test B — XML parser:**
```python
# Adjust the import to match whatever you named the file in Step 4
from codebase_search import parse_tool_calls
test_xml = """<tool_call>
<function=ripgrep>
<parameter=pattern>def main</parameter>
<parameter=path>src/</parameter>
</function>
</tool_call>

<tool_call>
<function=read>
<parameter=path>README.md</parameter>
</function>
</tool_call>

<tool_call>
<function=list_directory>
<parameter=path>.</parameter>
</function>
</tool_call>

<tool_call>
<function=finish>
<parameter=files>src/main.py:1-50</parameter>
</function>
</tool_call>"""

calls = parse_tool_calls(test_xml)
assert len(calls) == 4, f"Expected 4 tool calls, got {len(calls)}"
assert calls[0].name == "grep" and calls[0].args["pattern"] == "def main"
assert calls[1].name == "read" and calls[1].args["path"] == "README.md"
assert calls[2].name == "list_directory" and calls[2].args["path"] == "."
assert calls[3].name == "finish" and "files_raw" in calls[3].args
print("Parser: all 4 tool types parsed correctly")
```

**Test C — Internal tool executors:**
```python
from codebase_search import run_grep, run_read, run_list_dir  # adjust import

# Test each of the 4 internal tools against the current repo
repo = "."

# 1. grep
grep_result = run_grep(repo, "import", ".", "*.py")
assert "import" in grep_result or grep_result == "no matches", f"grep failed: {grep_result[:100]}"
print(f"grep: OK ({len(grep_result)} chars)")

# 2. read
read_result = run_read(repo, "codebase_search.py", 1, 10)  # use any .py file in your repo
assert "|" in read_result, f"read failed: {read_result[:100]}"
print(f"read: OK ({len(read_result)} chars)")

# 3. list_directory
list_result = run_list_dir(repo, ".")
assert len(list_result) > 0, f"list_dir failed: {list_result[:100]}"
print(f"list_dir: OK ({len(list_result)} chars)")

# 4. finish (tested implicitly via search — just verify the function exists)
from codebase_search import _resolve_finish  # adjust import
print("finish resolver: OK (importable)")

print("\nAll 4 internal tools working.")
```

**Test D — Full end-to-end search:**
```python
from codebase_search import search  # adjust import
results = search("Find the main entry point of this project", ".")
assert len(results) > 0, "Search returned no results"
for r in results:
    print(f"  Found: {r['path']} ({len(r['content'])} chars)")
print(f"\nEnd-to-end: OK — found {len(results)} files")
```

**Test E — Full agent integration:**
Send a message to your agent that requires using the tool, e.g.:
> "Use codebase_search to find how errors are handled in this project, then summarize what you found."

Verify the agent:
1. Calls the `codebase_search` tool (not grep or file read)
2. Passes a natural-language query (not a regex)
3. Receives results and summarizes them in its response

---

### Reference Documentation

- **Direct API Protocol:** https://docs.morphllm.com/sdk/components/warp-grep/direct
- **Python Guide:** https://docs.morphllm.com/guides/warp-grep-python
- **Examples (Python + multi-provider):** https://github.com/morphllm/examples/tree/main/warpgrep/python-agent
- **Pricing:** $0.80 / 1M tokens input, $0.80 / 1M tokens output
- **API Keys:** https://morphllm.com/dashboard/api-keys
