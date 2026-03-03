# WarpGrep + Vercel Sandbox

Code search subagent running inside an isolated sandbox.

```
sub·a·gent  /ˈsʌbˌeɪdʒənt/  noun

  An individual agent with its own separate context window that
  returns context to the parent agent without exceeding the sum
  of the tokens it processed.

  "The subagent searched 47 files across 3 directories and returned
   two relevant code blocks — 200 tokens back from 12,000 processed."
```

## How it works

```
  Opus 4.6
  |
  |
  '-------> WarpGrep (morph-warp-grep-v2)
            |
            |
            '-------> rg "middleware" src/**/*.ts        ──>  Vercel Sandbox (VM)
            |
            '-------> sed -n '156,168p' hono-base.ts    ──>  Vercel Sandbox
            |
            '-------> finish: 2 files, ~200 tokens
  |
  |
  '<-- tool_result: src/hono-base.ts:156-168, src/compose.ts
  |
  |
  (continues task with clean context window)
```

The parent never sees the 47 files WarpGrep scanned, the dead-end grep
results, or the directory listings. It gets back just the code that matters.

That's the point of a subagent: it burns tokens so the parent doesn't have to.

## In this example

The subagent runs inside a **Vercel Sandbox** — an isolated cloud VM.
The parent agent's `tool_use` triggers:

1. **Sandbox creation** — a fresh Amazon Linux 2023 VM spins up
2. **Repo clone + ripgrep install** — sets up the search environment
3. **WarpGrep agentic loop** — multiple turns of grep/read/listDir
   executed inside the sandbox via `sandbox.runCommand()`
4. **Result extraction** — only the relevant file snippets come back

## Setup

```bash
npm install
```

## Run

```bash
MORPH_API_KEY=sk-... ANTHROPIC_API_KEY=sk-... npx tsx index.ts \
  "https://github.com/honojs/hono.git" \
  "Find where middleware is registered"
```

## What you need

- `MORPH_API_KEY` — get one at [morphllm.com/dashboard](https://morphllm.com/dashboard)
- `ANTHROPIC_API_KEY` — for the parent agent (Claude)
- A Vercel account with Sandbox access (`vercel link` + `vercel env pull`)

## Docs

- [WarpGrep tool reference](https://docs.morphllm.com/sdk/components/warp-grep/tool) (see Vercel Sandbox tab)
- [WarpGrep overview](https://docs.morphllm.com/sdk/components/warp-grep/index)
- [Vercel Sandbox SDK](https://vercel.com/docs/sandbox)
