# WarpGrep: One-Prompt Integration

Two copy-paste prompts that add `codebase_search` to an agent codebase you already have. Hand one to an AI coding assistant (Claude Code, Cursor, Codex) pointed at your repo — it maps your agent's architecture, wires in the tool, and verifies it end to end.

These are prompts, not runnable examples. Nothing to install.

| Prompt | Use when |
|--------|----------|
| [one-prompt-integration-sdk.md](./one-prompt-integration-sdk.md) | Your agent is TypeScript/Node.js. Uses `@morphllm/morphsdk`, which handles the multi-turn search loop and ships adapters for Anthropic, OpenAI, Gemini, and the Vercel AI SDK. |
| [one-prompt-integration-api.md](./one-prompt-integration-api.md) | Your agent is anything else — Python, Go, Rust, Ruby. Uses the raw OpenAI-compatible HTTP API at `api.morphllm.com/v1`; you implement the multi-turn loop and the local tool executors yourself. |

Pick the SDK prompt if you can. It is less code and less to get wrong.

## Usage

1. Open the file that matches your stack
2. Copy everything below the `---` under **Prompt**
3. Paste it into your assistant with your agent repo open
4. The assistant works through the steps and checkpoints in order

Both prompts require a [Morph API key](https://morphllm.com/dashboard/api-keys) (`MORPH_API_KEY`). Local search also needs [ripgrep](https://github.com/BurntSushi/ripgrep).
