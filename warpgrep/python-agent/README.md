# WarpGrep: Python Agent

A complete WarpGrep implementation in Python. No SDK needed — just `openai` and `ripgrep`.

This is the same protocol that the TypeScript SDK uses under the hood. Use this as a reference for building WarpGrep integrations in any language.

## Prerequisites

- Python 3.10+
- [ripgrep](https://github.com/BurntSushi/ripgrep) installed (`brew install ripgrep` / `apt install ripgrep`)

## Setup

```bash
pip install -r requirements.txt
```

## Run

```bash
MORPH_API_KEY=your-key python search.py
```

Search a specific repo:

```bash
MORPH_API_KEY=your-key python search.py "Find auth middleware" /path/to/repo
```

## How it works

The agent runs a multi-turn conversation with the `morph-warp-grep-v2.1` model using OpenAI-compatible tool calling:

1. **Send** the repo structure + search query to the API (tools are built in — no `tools` parameter needed)
2. **Receive** structured `tool_calls` from the JSON response
3. **Execute** tools locally using ripgrep and file reads
4. **Send** results back as `tool` messages with `tool_call_id`
5. **Repeat** until the model calls `finish` (max 6 turns)

Each component (API client, tool executors, dispatcher, agent loop) is clearly separated in the code.
