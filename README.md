# Morph Examples

Runnable examples for Morph's subagent products.

## Find your example

Use this table to jump to the right place based on what you're building.

### By language

| I'm writing... | Go to |
|----------------|-------|
| **Python** | [`warpgrep/python-agent`](./warpgrep/python-agent) — full protocol reference, no SDK |
| **TypeScript** | [`warpgrep/basic-search`](./warpgrep/basic-search) — simplest starting point |
| **TypeScript + Anthropic SDK** | [`warpgrep/anthropic-agent`](./warpgrep/anthropic-agent) — Claude in an agent loop |
| **TypeScript + OpenAI SDK** | [`warpgrep/openai-agent`](./warpgrep/openai-agent) — GPT-4o in an agent loop |
| **TypeScript + Vercel AI SDK** | [`warpgrep/vercel-agent`](./warpgrep/vercel-agent) — automatic tool loop |
| **TypeScript + Google AI SDK** | [`warpgrep/gemini-agent`](./warpgrep/gemini-agent) — Gemini in an agent loop |

### By use case

| I want to... | Go to |
|--------------|-------|
| Search a local repo | [`warpgrep/basic-search`](./warpgrep/basic-search) |
| Search a GitHub repo (no clone) | [`warpgrep/github-search`](./warpgrep/github-search) |
| Stream search progress | [`warpgrep/streaming`](./warpgrep/streaming) |
| Run search in a Vercel Sandbox | [`warpgrep-vercel-sandbox`](./warpgrep-vercel-sandbox) |
| Run search in a Cloudflare Worker | [`warpgrep-cloudflare-sandbox`](./warpgrep-cloudflare-sandbox) |
| Build a PR review bot | [`github_app`](./github_app) |
| Benchmark PR review quality | [`pr_review_agent`](./pr_review_agent) |

### By platform

| Platform | Go to |
|----------|-------|
| **Local (CLI)** | [`warpgrep/`](./warpgrep) — all local examples |
| **Vercel Sandbox** | [`warpgrep-vercel-sandbox`](./warpgrep-vercel-sandbox) — isolated VM via `@vercel/sandbox` |
| **Cloudflare Workers** | [`warpgrep-cloudflare-sandbox`](./warpgrep-cloudflare-sandbox) — Durable Object sandbox via `@cloudflare/sandbox` |
| **GitHub App** | [`github_app`](./github_app) — deploy as a GitHub App |

## Examples

### WarpGrep — Code Search Subagent

All in [`warpgrep/`](./warpgrep):

| Example | Language | Description |
|---------|----------|-------------|
| [basic-search](./warpgrep/basic-search) | TypeScript | Query in, results out |
| [streaming](./warpgrep/streaming) | TypeScript | Stream search progress in real-time |
| [search-node-modules](./warpgrep/search-node-modules) | TypeScript | Search inside dependencies |
| [github-search](./warpgrep/github-search) | TypeScript | Search a public GitHub repo without cloning |
| [github-streaming](./warpgrep/github-streaming) | TypeScript | Stream GitHub search progress |
| [anthropic-agent](./warpgrep/anthropic-agent) | TypeScript | Claude + WarpGrep agent loop |
| [openai-agent](./warpgrep/openai-agent) | TypeScript | GPT-4o + WarpGrep agent loop |
| [vercel-agent](./warpgrep/vercel-agent) | TypeScript | Vercel AI SDK automatic tool loop |
| [gemini-agent](./warpgrep/gemini-agent) | TypeScript | Gemini + WarpGrep agent loop |
| [python-agent](./warpgrep/python-agent) | Python | Full protocol reference (no SDK) |

### WarpGrep — Sandbox Integrations

| Example | Platform | Description |
|---------|----------|-------------|
| [warpgrep-vercel-sandbox](./warpgrep-vercel-sandbox) | Vercel | Search code inside an isolated Vercel Sandbox VM |
| [warpgrep-cloudflare-sandbox](./warpgrep-cloudflare-sandbox) | Cloudflare | Search code inside a Cloudflare Worker with Durable Object sandbox |

### PR Review

| Example | Language | Description |
|---------|----------|-------------|
| [github_app](./github_app) | Python | Production GitHub App — Claude + WarpGrep multi-pass PR review |
| [pr_review_agent](./pr_review_agent) | Python | Benchmarking pipeline for PR review strategies |

## Prerequisites

- [Morph API key](https://morphllm.com/dashboard/api-keys) (`MORPH_API_KEY`) — all examples
- [ripgrep](https://github.com/BurntSushi/ripgrep) — local search examples only
- Node.js 18+ — TypeScript examples
- Python 3.10+ — Python examples
- `ANTHROPIC_API_KEY` — agent examples using Claude
- `OPENAI_API_KEY` — agent examples using GPT

## Quick start

```bash
cd warpgrep/basic-search
npm install
MORPH_API_KEY=your-key npx tsx search.ts "Find the main entry point" /path/to/repo
```

## Docs

- [WarpGrep overview](https://docs.morphllm.com/sdk/components/warp-grep)
- [WarpGrep tool reference](https://docs.morphllm.com/sdk/components/warp-grep/tool)
- [WarpGrep direct API](https://docs.morphllm.com/sdk/components/warp-grep/direct)
- [Python guide](https://docs.morphllm.com/guides/warp-grep-python)
