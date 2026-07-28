# Compact: Integration Prompts

Two copy-paste prompts that make a coding agent (Claude Code, Cursor, Codex, whatever you use) integrate Morph Compaction into an existing codebase. Paste the whole file into the agent, add your API key, and let it work.

Both prompts cover the same ground — where to put the compaction call, how to pick `compression_ratio` and `preserve_recent`, what to do with `<keepContext>` — and differ only in what they tell the agent to call.

| File | Use when |
|---|---|
| [one-prompt-integration-api.md](./one-prompt-integration-api.md) | The project is not TypeScript, or you don't want a new dependency. The agent calls `POST /v1/compact` over plain HTTP. |
| [one-prompt-integration-sdk.md](./one-prompt-integration-sdk.md) | The project already has an SDK you can point at Morph. Covers `@morphllm/morphsdk` for TypeScript (retries, types, edge runtime) and the OpenAI Python SDK with `model: "morph-compactor"`. |

If you're unsure: TypeScript or Python project, use the SDK prompt. Anything else, use the API prompt.
